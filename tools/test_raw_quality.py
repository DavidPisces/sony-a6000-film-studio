# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Original additions only; underlying third-party rights remain separate. See LICENSING.md and NOTICE.
"""Anchor, fail-before-write and idempotency checks for the RAW quality entries.

The pinned base APK is not redistributed, so these tests run against smali and
MenuData fragments that match the documented apktool output. They establish that
the injection lands in exactly one place, that an unexpected base changes nothing,
and that a second build pass is a no-op; they say nothing about camera behaviour.
"""
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from raw_quality import (ITEMS, LABELS, QUALITY_VALUES, quality_availability_method,
                         patch_quality_controller, patch_quality_menu)

BASE_CONTROLLER = 'com.sony.imaging.app.base.shooting.camera.PictureQualityController'
HOOK = 'Lcom/sony/imaging/app/pictureeffectplus/shooting/camera/RicohHook;'
# The pair of instructions around AvailableInfo in the pinned base class, with the
# blank line apktool 2.9.3 emits between the invoke and its move-result.
CONTROLLER = '''.method public getAvailableValue(Ljava/lang/String;)Ljava/util/List;
    .locals 9

    .prologue
    new-instance v0, Ljava/util/ArrayList;

    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V

    :cond_0
    move-result-object v3

    check-cast v3, Ljava/lang/String;

    const/4 v6, 0x2

    new-array v6, v6, [Ljava/lang/Object;

    const-string v8, "setPictureStorageFormat"

    aput-object v8, v6, v7

    aput-object v3, v6, v7

    invoke-static {v6}, Lcom/sony/imaging/app/util/AvailableInfo;->isAvailable([Ljava/lang/Object;)Z

    move-result v6

    if-eqz v6, :cond_0

    invoke-virtual {v0, v3}, Ljava/util/ArrayList;->add(Ljava/lang/Object;)Z

    goto :goto_0
.end method
'''

MENU = '''<?xml version="1.0" encoding="utf-8"?>
<Root>
  <Layer1 ItemId="Page1">
    <Layer1 ItemId="setPictureStorageFormat" ConfigClass="{base}" ExecType="NEXT_LAYOUT_WITHOUT_SET">
      <Layer2 ItemId="setPictureStorageFormat_jpegextrafine" Value="jpegextrafine" ExecType="SET_VALUE" />
      <Layer2 ItemId="setPictureStorageFormat_jpegfine" Value="jpegfine" ExecType="SET_VALUE" />
      <Layer2 ItemId="setPictureStorageFormat_jpegstandard" Value="jpegstandard" ExecType="SET_VALUE" />
    </Layer1>
  </Layer1>
</Root>
'''.format(base=BASE_CONTROLLER)


class RawQualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        (self.base / 'assets').mkdir()
        (self.base / 'assets/MenuData.xml').write_text(MENU)
        self.controller = self.base / 'smali/com/sony/imaging/app/base/shooting/camera/PictureQualityController.smali'
        self.controller.parent.mkdir(parents=True)
        self.controller.write_text(CONTROLLER)

    def menu_layer(self):
        tree = ET.parse(self.base / 'assets/MenuData.xml')
        return next(e for e in tree.getroot().iter() if e.get('ItemId') == 'setPictureStorageFormat')

    def test_availability_method_contract(self):
        method = quality_availability_method(HOOK)
        self.assertTrue(method.startswith('.method public static filterQualityAvailability'
                                         '(Ljava/lang/String;Z)Z'))
        self.assertTrue(method.endswith('.end method'))
        self.assertIn('    .locals 1\n', method)
        for value in QUALITY_VALUES:
            self.assertIn('"%s"' % value, method)
        # The untouched path must return Sony's own answer, and only a negative
        # answer may be overridden.
        self.assertIn('    :cond_orig_ok\n    return p1\n', method)
        self.assertIn('if-nez p1, :cond_orig_ok', method)
        with self.assertRaises(ValueError):
            quality_availability_method('not a descriptor')

    def test_controller_injection_keeps_sony_query_and_is_idempotent(self):
        patch_quality_controller(self.base, HOOK)
        text = self.controller.read_text()
        self.assertEqual(text.count('AvailableInfo;->isAvailable([Ljava/lang/Object;)Z'), 1)
        self.assertEqual(text.count('->filterQualityAvailability(Ljava/lang/String;Z)Z'), 1)
        # The override consumes the candidate value and reuses the result register.
        injected = ('    invoke-static {v3, v6}, ' + HOOK +
                    '->filterQualityAvailability(Ljava/lang/String;Z)Z\n\n'
                    '    move-result v6\n')
        self.assertEqual(text.count(injected), 1)
        self.assertLess(text.index('AvailableInfo;->isAvailable'),
                        text.index('filterQualityAvailability'))
        # The guarded add and the original branch label must survive unchanged.
        self.assertIn('if-eqz v6, :cond_0', text)
        self.assertIn('invoke-virtual {v0, v3}, Ljava/util/ArrayList;->add(Ljava/lang/Object;)Z',
                      text)
        once = text
        patch_quality_controller(self.base, HOOK)
        self.assertEqual(self.controller.read_text(), once)

    def test_controller_missing_anchor_changes_nothing(self):
        self.controller.write_text(CONTROLLER.replace('    move-result v6\n', ''))
        before = self.controller.read_bytes()
        with self.assertRaisesRegex(ValueError, 'exactly one getAvailableValue anchor'):
            patch_quality_controller(self.base, HOOK)
        self.assertEqual(self.controller.read_bytes(), before)

    def test_menu_entries_are_complete_and_idempotent(self):
        patch_quality_menu(self.base)
        layer = self.menu_layer()
        values = [child.get('Value') for child in layer]
        # The pinned upstream lists the RAW formats ahead of the JPEG ones.
        self.assertEqual(values[:2], ['rawjpeg', 'raw'])
        for child in layer:
            if child.get('Value') not in QUALITY_VALUES:
                continue
            self.assertEqual(child.get('ConfigClass'), BASE_CONTROLLER)
            self.assertEqual(child.get('ExecType'), 'SET_VALUE')
            self.assertEqual(child.get('ItemId'), 'setPictureStorageFormat_' + child.get('Value'))
            self.assertIn(child.get('ItemId'), LABELS)
            self.assertEqual(child.get('Title'), LABELS[child.get('ItemId')][0])
            self.assertIn('RAW', child.get('Title'))
            self.assertEqual(child.get('IconRes'), child.get('SelectedIconRes'))
            self.assertTrue(child.get('CautionID', '').startswith('CAUTION_GRP_ID_STILL_IMAGE_QUALITY_'))
            self.assertIsNone(child.get('GuideText'))
        once = (self.base / 'assets/MenuData.xml').read_bytes()
        patch_quality_menu(self.base)
        self.assertEqual((self.base / 'assets/MenuData.xml').read_bytes(), once)

    def test_menu_without_quality_layer_changes_nothing(self):
        path = self.base / 'assets/MenuData.xml'
        path.write_text(MENU.replace('ItemId="setPictureStorageFormat"', 'ItemId="something_else"'))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'no setPictureStorageFormat layer'):
            patch_quality_menu(self.base)
        self.assertEqual(path.read_bytes(), before)

    def test_preset_menu_labels_cover_both_values(self):
        for item_id, value, title, _caution, _icon, guide in ITEMS:
            self.assertEqual(LABELS[item_id][0], title)
            self.assertEqual(LABELS[item_id][1], guide)
            self.assertIn(value, QUALITY_VALUES)
            self.assertEqual(item_id, 'setPictureStorageFormat_' + value)
            self.assertTrue(title)
            self.assertTrue(guide)


if __name__ == '__main__':
    unittest.main()
