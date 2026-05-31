import os
import asyncio


# Pyrogram sync wrapper is incompatible with Python 3.14 default event loop behavior.
os.environ.setdefault("PYROGRAM_DISABLE_SYNC", "1")

try:
	asyncio.get_event_loop()
except RuntimeError:
	asyncio.set_event_loop(asyncio.new_event_loop())


# Patch save_file untuk guard self.me yang masih None.
#
# Pyrogram 2.0.106's `SaveFile.save_file` mengakses `self.me.is_premium` di
# baris 131 untuk menentukan limit ukuran file. Kalau client belum sempat
# memanggil `get_me()` (mis. langsung pakai session string + send_video),
# `self.me` masih None dan akses `.is_premium` melempar AttributeError yang
# muncul sebagai "Broadcast failed: 'NoneType' object has no attribute
# 'is_premium'" saat broadcast dengan attachment.
#
# IMPORTANT: pyrogram/sync.py menjalankan `wrap(Methods)` di load-time, yang
# membungkus tiap async method `Methods.<name>` dengan sync wrapper dan
# menyimpan referensi ke fungsi async asli di closure. Jadi:
#   - `SaveFile.save_file` masih async asli (tidak terbungkus).
#   - `Methods.save_file` adalah sync wrapper yang membawa closure ke async
#     ASLI (bukan ke patch kita kalau kita hanya mem-patch SaveFile).
#
# Karena `Client` mewarisi dari `Methods`, attribute lookup `client.save_file`
# resolve ke `Methods.save_file` dulu, sehingga patch yang hanya menyentuh
# `SaveFile.save_file` TIDAK pernah terpanggil. Itu sebabnya broadcast media
# tetap gagal walau patch sebelumnya ada.
#
# Fix: patch `Methods.save_file` (yang diresolve oleh Client) DAN
# `SaveFile.save_file` (untuk konsistensi kalau kode lain memanggil via
# SaveFile langsung). Patch berbentuk async function — saat dipanggil dari
# konteks async (await client.save_file(...)), Python akan await coroutine
# dari fungsi kita → berfungsi seperti async method biasa.
try:
	from pyrogram.methods import Methods
	from pyrogram.methods.advanced.save_file import SaveFile

	_ORIG_SAVE_FILE = SaveFile.save_file

	if not getattr(Methods.save_file, "_teleblaster_me_patch", False):
		async def _save_file_with_me_guard(self, *args, **kwargs):
			if getattr(self, "me", None) is None:
				try:
					me = await self.get_me()
					self.me = me
				except Exception:
					class _MeFallback:
						is_premium = False

					self.me = _MeFallback()
			elif getattr(self.me, "is_premium", None) is None:
				try:
					self.me.is_premium = False
				except Exception:
					pass

			return await _ORIG_SAVE_FILE(self, *args, **kwargs)

		_save_file_with_me_guard._teleblaster_me_patch = True
		Methods.save_file = _save_file_with_me_guard
		SaveFile.save_file = _save_file_with_me_guard
except Exception:
	# Keep app running even if internal API layout changes in future Pyrogram versions.
	pass
