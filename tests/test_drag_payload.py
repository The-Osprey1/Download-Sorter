"""A file dragged out of the shelf or the quick switcher carries a CF_HDROP file list (#28).

Explorer understands the shell's own item list, but most other apps (browsers, chat apps,
slicers) only read CF_HDROP. The drag object used to offer none; this checks it now does.
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
from pathlib import Path

import batch_handoff


@unittest.skipUnless(sys.platform == "win32", "Windows shell drag objects")
class DragPayloadTests(unittest.TestCase):
    def _hdrop_files(self, data_object) -> list[str]:
        from ctypes import wintypes

        class FORMATETC(ctypes.Structure):
            _fields_ = [("cfFormat", wintypes.WORD), ("ptd", ctypes.c_void_p), ("dwAspect", wintypes.DWORD),
                        ("lindex", wintypes.LONG), ("tymed", wintypes.DWORD)]

        class STGMEDIUM(ctypes.Structure):
            _fields_ = [("tymed", wintypes.DWORD), ("handle", ctypes.c_void_p), ("pUnk", ctypes.c_void_p)]

        vtable = ctypes.cast(ctypes.cast(data_object, ctypes.POINTER(ctypes.c_void_p))[0],
                             ctypes.POINTER(ctypes.c_void_p))
        get_data = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(FORMATETC),
                                      ctypes.POINTER(STGMEDIUM))(vtable[3])
        medium = STGMEDIUM()
        hr = get_data(data_object, ctypes.byref(FORMATETC(15, None, 1, -1, 1)), ctypes.byref(medium))
        self.assertEqual(hr, 0, "the drag object offers no CF_HDROP file list")
        shell32 = ctypes.windll.shell32
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        files = []
        for index in range(shell32.DragQueryFileW(medium.handle, 0xFFFFFFFF, None, 0)):
            buf = ctypes.create_unicode_buffer(1024)
            shell32.DragQueryFileW(medium.handle, index, buf, 1024)
            files.append(buf.value)
        ctypes.windll.ole32.ReleaseStgMedium(ctypes.byref(medium))
        return files

    def test_drag_object_lists_the_files(self) -> None:
        ctypes.windll.ole32.OleInitialize(None)
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "first.stl"
            b = Path(tmp) / "second file.3mf"
            a.write_text("x", encoding="utf-8")
            b.write_text("y", encoding="utf-8")
            data_object = batch_handoff.create_file_data_object([a, b])
            self.assertIsNotNone(data_object)
            try:
                files = self._hdrop_files(data_object)
            finally:
                batch_handoff.release_com(data_object)
        self.assertEqual([Path(f).name for f in files], ["first.stl", "second file.3mf"])


if __name__ == "__main__":
    unittest.main()
