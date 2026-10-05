"""Read default Windows audio endpoint levels/mute. Never changes settings."""
import ctypes as c
import json
from comtypes import GUID, IUnknown, COMMETHOD, HRESULT, CoCreateInstance, CLSCTX_ALL


class Device(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [COMMETHOD([], HRESULT, "Activate",
                          (["in"], c.POINTER(GUID), "iid"),
                          (["in"], c.c_ulong, "context"),
                          (["in"], c.c_void_p, "params"),
                          (["out"], c.POINTER(c.POINTER(IUnknown)), "interface"))]


class Enumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [COMMETHOD([], HRESULT, "EnumAudioEndpoints"),
                 COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                           (["in"], c.c_int, "flow"), (["in"], c.c_int, "role"),
                           (["out"], c.POINTER(c.POINTER(Device)), "device"))]


class EndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    # Unused slots retain ABI order; this test exposes no setters to callers.
    _methods_ = [COMMETHOD([], HRESULT, name) for name in
                 ("RegisterControlChangeNotify", "UnregisterControlChangeNotify", "GetChannelCount",
                  "SetMasterVolumeLevel", "SetMasterVolumeLevelScalar", "GetMasterVolumeLevel")]
    _methods_ += [COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar",
                           (["out"], c.POINTER(c.c_float), "level"))]
    _methods_ += [COMMETHOD([], HRESULT, name) for name in
                  ("SetChannelVolumeLevel", "SetChannelVolumeLevelScalar", "GetChannelVolumeLevel",
                   "GetChannelVolumeLevelScalar", "SetMute")]
    _methods_ += [COMMETHOD([], HRESULT, "GetMute", (["out"], c.POINTER(c.c_int), "muted"))]


if __name__ == "__main__":
    enumerator = CoCreateInstance(GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
                                  interface=Enumerator, clsctx=CLSCTX_ALL)
    for name, flow in (("output", 0), ("microphone", 1)):
        device = enumerator.GetDefaultAudioEndpoint(flow, 1)
        endpoint = device.Activate(c.byref(EndpointVolume._iid_), CLSCTX_ALL, None).QueryInterface(EndpointVolume)
        print(json.dumps(dict(device=name, muted=bool(endpoint.GetMute()),
                              volume_percent=round(endpoint.GetMasterVolumeLevelScalar()*100, 1))))
