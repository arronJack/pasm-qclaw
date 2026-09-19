# -*- coding: utf-8 -*-
"""Windows 音频自愈与诊断（纯 ctypes COM，零第三方依赖）。

背景：winsound / mciSendString 都"播放成功"但用户可能听不到 —— 常见根因：
1) 默认播放端点被静音 / 音量被拉到 0（含被"音量合成器"单独静音前的最常见场景）；
2) 默认输出指向了不会发声的设备（显示器 HDMI/Display Audio 等）。

模块在每次发声前做一次轻量自愈：读默认端点的静音与音量 → 取消静音、把音量
拉回合理值 → 返回可读诊断文本（设备名 / 是否修复 / 端点列表），供界面展示。
"""
import ctypes
import os
import re
import winreg
from ctypes import wintypes
from uuid import UUID

_ole32 = ctypes.OleDLL("ole32")
_ole32.CoInitializeEx(None, 0x2)          # COINIT_APARTMENTTHREADED
CLSCTX_ALL = 23
E_RENDER, E_CONSOLE = 0, 0
HRESULT = ctypes.HRESULT
LPGUID = ctypes.c_void_p


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    def __init__(self, s=None):
        super().__init__()
        if s:
            u = UUID(s)
            self.Data1 = u.time_low
            self.Data2 = u.time_mid & 0xFFFF
            self.Data3 = u.time_hi_version & 0xFFFF
            self.Data4 = (wintypes.BYTE * 8)(
                *(bytes([u.clock_seq_hi_variant, u.clock_seq_low])
                  + u.node.to_bytes(6, "big")))


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioEndpointVolume = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")

# 常见"不会发声"的默认端点关键字（显示器/采集等）
_SILENT_HINT = re.compile(r"display|hdmi|monitor|audio for|digital output", re.I)

# v0.18.0：默认**绝不动系统音量/静音**——只做只读诊断。
# 旧版"音量过低自动拉回 60%"会打断用户原本的音量（真机反馈：每次朗读音量被改成 60%）。
# 仅当用户在设置页勾选「允许自动调整系统音量」时，才由 companion 把本开关置 True。
ALLOW_TWEAK = False
_NAME_KEY = r"{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_RENDER = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"


def _vtbl(obj):
    return ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))


def _fn(obj, idx, restype, argtypes):
    """取 COM vtable 第 idx 个槽并包成可调用函数。"""
    fp = _vtbl(obj)[idx]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(fp)


def _endpoint_name(guid_or_id: str) -> str:
    """把端点 GUID（或 MMDevice id）映射成友好设备名。

    MMDevice 默认端点 id 形如 {0.0.0.00000000}.{e5155c83-…} —— 取其中的
    {e5155c83-…} 段即注册表 Render 键名，可查到友好名。
    """
    m = re.findall(r"\{([0-9a-fA-F-]{36})\}", guid_or_id or "")
    cands = [guid_or_id]
    for c in (reversed(m) if m else []):
        cands.append(c)
        cands.append("{" + c + "}")
    for cand in cands:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RENDER + "\\" + cand)
            try:
                p = winreg.OpenKey(k, "Properties")
                try:
                    return winreg.QueryValueEx(p, _NAME_KEY)[0]
                finally:
                    winreg.CloseKey(p)
            finally:
                winreg.CloseKey(k)
        except Exception:
            continue
    return guid_or_id


def list_render_endpoints() -> list:
    """所有渲染端点 (guid, name, state)，state=1 为活动。"""
    out = []
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RENDER)
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(k, i)
                i += 1
            except OSError:
                break
            try:
                ek = winreg.OpenKey(k, guid)
                try:
                    state = winreg.QueryValueEx(ek, "DeviceState")[0] & 0xF
                finally:
                    winreg.CloseKey(ek)
                out.append((guid, _endpoint_name(guid), state))
            except Exception:
                continue
        winreg.CloseKey(k)
    except Exception:
        pass
    return out


def default_endpoint_guid() -> str | None:
    """读取当前默认（多媒体/控制台角色）渲染端点 GUID。"""
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RENDER)
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(k, i)
                i += 1
            except OSError:
                break
            try:
                role = winreg.OpenKey(k, guid + "\\Role")
                try:
                    for n in range(4):
                        try:
                            if winreg.QueryValueEx(role, str(n))[0] == 1:
                                return guid
                        except OSError:
                            pass
                finally:
                    winreg.CloseKey(role)
            except OSError:
                continue
        winreg.CloseKey(k)
    except Exception:
        pass
    return None


def heal_and_diagnose(adjust: bool | None = None) -> str:
    """读取默认端点 → 返回可读诊断（供 UI/voice.log 使用）。

    v0.18.0：默认（adjust 为 None/False 时）**只读不改**——无论静音还是音量多低，
    都不碰系统设置，只如实报告，把决定权留给用户。
    仅当 adjust=True（= 设置页「允许自动调整系统音量」已勾选）时才：
    取消静音 + 音量过低(<35%)时拉回 60%。
    全程 try/except，任何一步失败都不影响调用方（自愈尽力而为）。
    """
    if adjust is None:
        adjust = ALLOW_TWEAK
    lines = []
    try:
        endpoints = [e for e in list_render_endpoints() if e[2] == 1]
        active = "、".join(e[1] for e in endpoints) or "（无活动输出端点）"
        lines.append("活动输出端点：" + active)
    except Exception as ex:
        lines.append("枚举输出端点失败：" + str(ex))
    dev = None
    # ---- COM 拿默认端点并操作 IAudioEndpointVolume ----
    enum_obj = ctypes.c_void_p()
    try:
        hr = _ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None,
                                     CLSCTX_ALL, ctypes.byref(IID_IMMDeviceEnumerator),
                                     ctypes.byref(enum_obj))
        if hr != 0 or not enum_obj.value:
            lines.append("COM 初始化失败（hr=0x%08X）" % (hr & 0xFFFFFFFF))
            return "\n".join(lines)
        GetDefault = _fn(enum_obj, 4, HRESULT,
                         [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])
        dev = ctypes.c_void_p()
        hr = GetDefault(enum_obj, E_RENDER, E_CONSOLE, ctypes.byref(dev))
        if hr != 0 or not dev.value:
            lines.append("获取默认播放设备失败（hr=0x%08X）" % (hr & 0xFFFFFFFF))
            return "\n".join(lines)
        # 设备名（先注册表默认角色，后 COM GetId 尾段）
        dev_id = ""
        try:
            GetId = _fn(dev, 5, HRESULT, [ctypes.POINTER(wintypes.LPWSTR)])
            pid = wintypes.LPWSTR()
            if GetId(dev, ctypes.byref(pid)) == 0 and pid.value:
                dev_id = pid.value
                _ole32.CoTaskMemFree(pid)
        except Exception:
            pass
        dev_name = _endpoint_name(dev_id) if dev_id else _endpoint_name(
            default_endpoint_guid())
        lines.append("默认播放设备：" + dev_name)
        # Activate IAudioEndpointVolume
        Activate = _fn(dev, 3, HRESULT,
                       [ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p)])
        vol = ctypes.c_void_p()
        hr = Activate(dev, ctypes.byref(IID_IAudioEndpointVolume),
                      CLSCTX_ALL, None, ctypes.byref(vol))
        if hr != 0 or not vol.value:
            lines.append("无法访问音量接口（可能无权限，hr=0x%08X）" % (hr & 0xFFFFFFFF))
            return "\n".join(lines)
        GetMute = _fn(vol, 15, HRESULT, [ctypes.POINTER(wintypes.BOOL)])
        SetMute = _fn(vol, 14, HRESULT, [wintypes.BOOL, LPGUID])
        GetVol = _fn(vol, 9, HRESULT, [ctypes.POINTER(ctypes.c_float)])
        SetVol = _fn(vol, 7, HRESULT, [ctypes.c_float, LPGUID])
        muted = wintypes.BOOL(0)
        level = ctypes.c_float(1.0)
        if GetMute(vol, ctypes.byref(muted)) == 0:
            if muted.value:
                if adjust and SetMute(vol, False, None) == 0:
                    lines.append("发现默认设备被静音 → 已自动取消")
                else:
                    lines.append("默认设备处于静音"
                                 + ("（自动取消失败，请在系统音量里打开）" if adjust
                                    else "（未改动——需要我自动解除时，请在设置页勾选"
                                         "「允许自动调整系统音量」）"))
            else:
                lines.append("默认设备未静音")
        if GetVol(vol, ctypes.byref(level)) == 0:
            v = max(0.0, min(1.0, level.value))
            if adjust and v < 0.35:          # 仅用户明确允许时才代调音量
                if SetVol(vol, 0.6, None) == 0:
                    lines.append("发现音量偏低（%d%%）→ 已自动调到 60%%"
                                 % round(v * 100))
                else:
                    lines.append("音量偏低（自动调高失败，请在系统音量里调大）")
            else:
                lines.append("默认设备音量：%d%%（未改动）" % round(v * 100))
        # 默认设备是无发声能力的显示器输出？提示换设备
        if re.search(_SILENT_HINT, dev_name) and any(
                re.search(r"speaker|耳机|headphone|扬声|realtek", e[1], re.I)
                for e in endpoints):
            lines.append("⚠ 默认输出像是显示器/数字音频（可能无声），"
                         "而你另有扬声器/耳机——请在系统声音设置里把默认输出"
                         "切到扬声器。")
        return "\n".join(lines)
    except Exception as ex:
        lines.append("音频诊断异常：" + str(ex))
        return "\n".join(lines)


def ensure_audible() -> bool:
    """发声前诊断一次（默认只读，不动系统音量），返回是否至少有可用的活动输出端点。"""
    try:
        any_active = any(e[2] == 1 for e in list_render_endpoints())
    except Exception:
        any_active = True
    try:
        heal_and_diagnose()
    except Exception:
        pass
    return any_active
