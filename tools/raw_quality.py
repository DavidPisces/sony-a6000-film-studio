# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Original additions only; underlying third-party rights remain separate. See LICENSING.md and NOTICE.
"""Offer the native RAW / RAW+JPEG still-quality entries this app is denied.

Sony ships "raw" and "rawjpeg" in PictureQualityController.PICTURE_QUALITY_COMMANDS
and only removes them from getAvailableValue() through AvailableInfo, whose
inhibition records live in the camera's own database. Nothing here adds a hardware
capability: getAvailableValue() iterates the list returned by
CameraEx$ParametersModifier.getSupportedPictureStorageFormats(), so the hook is
only ever asked about formats the body already reports, and every other answer
passes through unchanged.

Both halves are required. The controller override makes the formats selectable;
the MenuData entries give the menu something to display. Selecting either value
still runs through Sony's own PictureQualityController.setValue() ->
ParametersModifier.setPictureStorageFormat(), so capture keeps the native path and
no picture-taking code is replaced.
"""
import re
import xml.etree.ElementTree as ET

LAYER1_ID = 'setPictureStorageFormat'
BASE_CONTROLLER = 'com.sony.imaging.app.base.shooting.camera.PictureQualityController'
CONTROLLER_PATH = 'smali/com/sony/imaging/app/base/shooting/camera/PictureQualityController.smali'
QUALITY_VALUES = ('raw', 'rawjpeg')
# Every still-quality icon in the pinned MenuData is framework-namespaced
# (android:drawable/p_stillquality_set_n_00x), because the native quality badges
# belong to the camera framework rather than to this app. The pinned upstream
# module omits that prefix; the name cannot resolve inside this APK, whose
# resources.arsc contains no "imgquality" resource at all. Verify on camera.
# Zero-risk fallbacks if the framework name is wrong: the IconRes already used by
# the JPEG entries in this same layer, or INVALID_RESID for no icon.
RAW_ICON = 'android:drawable/p_16_dd_parts_5w_shoot_icon_imgquality_uncompressed_raw'
RAW_JPEG_ICON = RAW_ICON + '_j'
# CautionID values are existing constants in com.sony.imaging.app.base.caution.CautionID.
ITEMS = (
    ('setPictureStorageFormat_raw', 'raw', 'RAW',
     'CAUTION_GRP_ID_STILL_IMAGE_QUALITY_RAW_INVALID_GUIDE', RAW_ICON,
     '只记录 RAW 图像，不生成 JPEG。'),
    ('setPictureStorageFormat_rawjpeg', 'rawjpeg', 'RAW & JPEG',
     'CAUTION_GRP_ID_STILL_IMAGE_QUALITY_RAW_JPEG_INVALID_GUIDE', RAW_JPEG_ICON,
     '同时记录 RAW 与 JPEG 图像；RAW 不受滤镜影响。'),
)
# The pinned base already routes BaseMenuService.getMenuItemText/getMenuItemGuideText
# through the hook by ItemId, so names and guides are delivered here. Title and
# DisplayName are also written into MenuData as a fallback, matching how the film
# presets are labelled. The stock MenuData has no GuideText attribute, so none is
# invented; guides reach the camera only through the hook.
LABELS = {item: (title, guide) for item, _value, title, _caution, _icon, guide in ITEMS}

# One pair of instructions in PictureQualityController.getAvailableValue():
# v3 holds the candidate quality value and v6 holds AvailableInfo's answer.
# Blank lines are optional because apktool versions space them differently.
ANCHOR = re.compile(
    r'(    invoke-static \{v6\}, Lcom/sony/imaging/app/util/AvailableInfo;->isAvailable'
    r'\(\[Ljava/lang/Object;\)Z\n'
    r'(?:[ \t]*\n)*'
    r'    move-result v6\n)')


def quality_availability_method(hook):
    """The hook entry that PictureQualityController.getAvailableValue() calls."""
    if not hook.startswith('L') or not hook.endswith(';'):
        raise ValueError('Expected a smali class descriptor')
    return f'''.method public static filterQualityAvailability(Ljava/lang/String;Z)Z
    .locals 1

    if-nez p1, :cond_orig_ok

    const-string v0, "rawjpeg"
    invoke-virtual {{v0, p0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :cond_check_raw

    const/4 v0, 0x1
    return v0

    :cond_check_raw
    const-string v0, "raw"
    invoke-virtual {{v0, p0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :cond_orig_ok

    const/4 v0, 0x1
    return v0

    :cond_orig_ok
    return p1
.end method'''


def patch_quality_controller(base, hook):
    """Keep Sony's availability query, then re-open the formats it hides."""
    path = base / CONTROLLER_PATH
    if not path.is_file():
        raise ValueError('Missing ' + CONTROLLER_PATH)
    text = path.read_text()
    if 'filterQualityAvailability' in text:
        print('getAvailableValue already re-opens the RAW formats')
        return
    def inject(match):
        # v3 is the candidate value; move-result reuses v6 that Sony just filled.
        return match.group(1) + (
            f'\n    invoke-static {{v3, v6}}, {hook}->filterQualityAvailability'
            '(Ljava/lang/String;Z)Z\n\n    move-result v6\n')
    updated, count = ANCHOR.subn(inject, text)
    if count != 1:
        raise ValueError('Expected exactly one getAvailableValue anchor, found %d' % count)
    # Write only after the anchor proved unique, so a wrong base APK changes nothing.
    path.write_text(updated)
    print('Re-opened RAW and RAW+JPEG in PictureQualityController.getAvailableValue()')


def patch_quality_menu(base):
    """Add the two Layer2 entries the firmware already knows how to execute."""
    path = base / 'assets/MenuData.xml'
    tree = ET.parse(path)
    layer1 = next((node for node in tree.getroot().iter() if node.get('ItemId') == LAYER1_ID),
                  None)
    if layer1 is None:
        raise ValueError('MenuData has no %s layer' % LAYER1_ID)
    present = {child.get('Value') for child in layer1}
    added = 0
    for item, value, title, caution, icon, _guide in ITEMS:
        if value in present:
            continue
        entry = ET.Element('Layer2', dict(
            CautionID=caution, ConfigClass=BASE_CONTROLLER, DisplayName=title,
            ExecType='SET_VALUE', IconRes=icon, ItemId=item,
            SelectedIconRes=icon, Title=title, Value=value))
        # The pinned upstream lists the RAW formats ahead of the JPEG ones.
        layer1.insert(0, entry)
        added += 1
    if not added:
        print('RAW quality menu entries already present')
        return
    tree.write(path, encoding='utf-8', xml_declaration=True)
    print('Added %d RAW quality menu entries to %s' % (added, LAYER1_ID))
