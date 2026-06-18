"""
jvdl_client/_downloader_32bit.py
=================================
32-bit COM ダウンローダー。py -3.13-32 サブプロセスとして実行される。

JV-Link は 32-bit COM コンポーネントのため、64-bit Python から
直接 CreateObject できない (REGDB_E_CLASSNOTREG)。
このスクリプトを 32-bit Python で起動することで COM 呼び出しを実現する。

JVGets は pywin32 の遅延バインディングではクラッシュするため、
comtypes + 型ライブラリによる早期バインディング + raw vtable 経由で呼び出す。
(詳細は loader.py の修正履歴 Fix 4-6 を参照)

Usage:
    py -3.13-32 -m jvdl_client._downloader_32bit DATASPEC FROM_TIME OPTION OUTPUT_FILE

Exit codes:
    0 - success
    1 - error
"""
import ctypes
import struct
import sys
import time
import traceback


def main() -> None:
    if len(sys.argv) != 5:
        print(
            f"Usage: {sys.argv[0]} DATASPEC FROM_TIME OPTION OUTPUT_FILE",
            file=sys.stderr,
        )
        sys.exit(1)

    dataspec = sys.argv[1]
    from_time = sys.argv[2]
    option = int(sys.argv[3])
    output_file = sys.argv[4]

    try:
        import comtypes
        import comtypes.client
    except ImportError as e:
        print(f"[32bit] comtypes not available: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    try:
        print(f"[32bit] Streaming {dataspec} from={from_time}, option={option}", flush=True)

        # 型ライブラリから Python バインディングを生成（冪等）
        comtypes.client.GetModule(r"C:\WINDOWS\SysWow64\JVDTLAB\JVDTLab.dll")

        JVLINK_CLSID = comtypes.GUID("{2AB1774D-0C41-11D7-916F-0003479BEB3F}")
        jv = comtypes.client.CreateObject(JVLINK_CLSID)

        ret = jv.JVInit("UNKNOWN")
        if ret != 0:
            raise RuntimeError(f"JVInit failed: ret={ret}")

        # JVOpen — comtypes は [readcount, downloadcount, lastfile_ts, ret_code] を返す
        open_result = jv.JVOpen(dataspec, from_time, option)
        readcount, downloadcount, lastfile_ts, ret = (
            open_result[0], open_result[1], open_result[2], open_result[3],
        )
        print(
            f"[32bit] JVOpen ret={ret}, readcount={readcount}, downloadcount={downloadcount}",
            flush=True,
        )
        if ret == -1:
            # from_time 以降の新規データがない (正常ケース: 差分なし)
            print(f"[32bit] No new data since {from_time} — writing empty file.", flush=True)
            open(output_file, "wb").close()
            return
        if ret < 0:
            raise RuntimeError(f"JVOpen failed: ret={ret}")

        # ── Raw vtable アクセス (JVGets) ────────────────────────────────────────
        # vtable レイアウト (32-bit 確認済み):
        #   IUnknown[0-2] + IDispatch[3-6] + IJVLink カスタムメソッドは [7] から
        #   IJVLink._methods_ index 21 = JVGets → vtbl[28]
        iface_ptr = ctypes.cast(jv, ctypes.c_void_p).value
        vtbl_ptr = ctypes.cast(iface_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
        vtbl = ctypes.cast(vtbl_ptr, ctypes.POINTER(ctypes.c_void_p))
        jvgets_raw_fn = vtbl[28]

        # HRESULT __stdcall JVGets(this, VARIANT* buff, LONG nBuffLen, BSTR* fn, LONG* ret)
        JVGETS_PROTO = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,                  # this
            ctypes.c_void_p,                  # VARIANT* buff (raw)
            ctypes.c_long,                    # LONG nBuffLen
            ctypes.POINTER(ctypes.c_void_p),  # BSTR* lpszFileName
            ctypes.POINTER(ctypes.c_long),    # LONG* lpRet
        )
        jvgets_fn = JVGETS_PROTO(jvgets_raw_fn)

        oleaut = ctypes.windll.oleaut32
        oleaut.SysFreeString.restype = None
        oleaut.SysFreeString.argtypes = [ctypes.c_void_p]
        oleaut.SafeArrayAccessData.restype = ctypes.HRESULT
        oleaut.SafeArrayAccessData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        oleaut.SafeArrayUnaccessData.restype = ctypes.HRESULT
        oleaut.SafeArrayUnaccessData.argtypes = [ctypes.c_void_p]
        oleaut.SafeArrayDestroy.restype = ctypes.HRESULT
        oleaut.SafeArrayDestroy.argtypes = [ctypes.c_void_p]

        BUFF_SIZE = 40010
        # VT_ARRAY|VT_UI1 = 0x2011
        # VB の `Dim bytData() As Byte` (未初期化動的配列) を COM に渡す形式。
        # JVLink はこの型識別子を見て Byte SafeArray として動的確保・書き込みを行う。
        VT_ARRAY_UI1 = 0x2011

        # JVOpen 後、downloadcount > 0 なら DL 完了を待つ
        if downloadcount > 0:
            print(f"[32bit] Waiting for download... (downloadcount={downloadcount})", flush=True)
            wait_secs = 0
            while jv.JVStatus() == 0:
                time.sleep(1)
                wait_secs += 1
                if wait_secs % 30 == 0:
                    print(f"[32bit] Still waiting... ({wait_secs}s)", flush=True)
                if wait_secs > 1800:
                    raise RuntimeError(f"JVLink download timeout after {wait_secs}s")

        count = 0
        with open(output_file, "wb") as f:
            while True:
                # VARIANT レイアウト (32-bit, 16 bytes):
                #   offset 0: vt (WORD) — VT_ARRAY|VT_UI1
                #   offset 2: reserved (6 bytes)
                #   offset 8: parray (DWORD — SAFEARRAY* ポインタ, 32-bit) = 0 で渡す
                var_data = bytearray(16)
                struct.pack_into("<H", var_data, 0, VT_ARRAY_UI1)
                var_arr = (ctypes.c_byte * 16).from_buffer(var_data)

                fn_bstr = ctypes.c_void_p(0)
                rc_out = ctypes.c_long(0)

                jvgets_fn(
                    iface_ptr,
                    ctypes.addressof(var_arr),
                    BUFF_SIZE,
                    ctypes.byref(fn_bstr),
                    ctypes.byref(rc_out),
                )
                rc = rc_out.value
                parray = struct.unpack_from("<I", var_data, 8)[0]

                if rc == 0:
                    # ストリーム終端
                    if parray:
                        oleaut.SafeArrayDestroy(parray)
                    if fn_bstr.value:
                        oleaut.SysFreeString(fn_bstr.value)
                    break

                elif rc == -1:
                    # ファイル切替 — 次へ
                    if parray:
                        oleaut.SafeArrayDestroy(parray)
                    if fn_bstr.value:
                        oleaut.SysFreeString(fn_bstr.value)
                    continue

                elif rc in (-3, -402):
                    # ダウンロード待ち — リトライ
                    if parray:
                        oleaut.SafeArrayDestroy(parray)
                    if fn_bstr.value:
                        oleaut.SysFreeString(fn_bstr.value)
                    time.sleep(1)
                    continue

                elif rc < 0:
                    print(f"[32bit] JVGets error rc={rc}", file=sys.stderr, flush=True)
                    if parray:
                        oleaut.SafeArrayDestroy(parray)
                    if fn_bstr.value:
                        oleaut.SysFreeString(fn_bstr.value)
                    break

                else:
                    # rc > 0: rc バイトの CP932 レコードデータ
                    if parray and rc > 0:
                        data_ptr = ctypes.c_void_p()
                        oleaut.SafeArrayAccessData(parray, ctypes.byref(data_ptr))
                        raw_bytes = bytes((ctypes.c_ubyte * rc).from_address(data_ptr.value))
                        oleaut.SafeArrayUnaccessData(parray)
                        f.write(raw_bytes)
                        f.write(b"\n")
                        count += 1
                    if parray:
                        oleaut.SafeArrayDestroy(parray)
                    if fn_bstr.value:
                        oleaut.SysFreeString(fn_bstr.value)

                    if count % 5000 == 0 and count > 0:
                        print(f"[32bit] Streamed {count} records...", flush=True)

        jv.JVClose()
        print(f"[32bit] Done. {count} records → {output_file}", flush=True)

    except Exception as e:
        print(f"[32bit] Fatal: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
