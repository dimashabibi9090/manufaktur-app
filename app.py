import datetime
import math

import pandas as pd
import psycopg2
import streamlit as st
from psycopg2 import errors

st.set_page_config(page_title="Sistem Rantai Pasok & Manufaktur", layout="wide")

TABEL = [
    "pemasok", "bahan_baku", "pembelian", "detail_pembelian",
    "produk", "bill_of_material", "bengkel_mitra", "produksi", "penjualan",
]

# Pengaturan 4 tabel data master: nama tabel, kolom kunci, awalan kode, dan kolom isian
MASTER = {
    "Pemasok": {
        "tabel": "pemasok", "pk": "id_pemasok", "prefix": "PMK",
        "kolom": [
            ("nama_pemasok", "Nama pemasok", "text"),
            ("alamat", "Alamat", "text"),
            ("kontak", "Kontak", "text"),
        ],
    },
    "Bahan Baku": {
        "tabel": "bahan_baku", "pk": "id_bahan", "prefix": "BHN",
        "kolom": [
            ("nama_bahan", "Nama bahan", "text"),
            ("satuan", "Satuan (lembar, pcs, set, ...)", "text"),
            ("stok", "Stok", "int"),
        ],
    },
    "Produk": {
        "tabel": "produk", "pk": "id_produk", "prefix": "PRD",
        "kolom": [
            ("nama_produk", "Nama produk", "text"),
            ("harga_jual", "Harga jual (Rp)", "uang"),
            ("stok_produk_jadi", "Stok produk jadi", "int"),
        ],
    },
    "Bengkel Mitra": {
        "tabel": "bengkel_mitra", "pk": "id_bengkel", "prefix": "BKL",
        "kolom": [
            ("nama_bengkel", "Nama bengkel", "text"),
            ("alamat", "Alamat", "text"),
            ("kontak", "Kontak", "text"),
            ("tarif", "Tarif (Rp)", "uang"),
        ],
    },
}


# ---------- Koneksi database ----------
def get_conn():
    s = st.secrets["db"]
    return psycopg2.connect(
        host=s["host"],
        port=s["port"],
        dbname=s["dbname"],
        user=s["user"],
        password=s["password"],
        sslmode="require",
    )


def query(sql, params=None):
    """Menjalankan SELECT dan mengembalikan hasilnya sebagai tabel."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            kolom = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=kolom)
    finally:
        conn.close()


def run(sql, params=None):
    """Menjalankan INSERT / UPDATE / DELETE."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    finally:
        conn.close()


def run_many(daftar):
    """Menjalankan beberapa perintah sekaligus dalam satu transaksi.
    Kalau satu perintah gagal, semuanya dibatalkan (tidak ada data setengah jadi)."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                for sql, params in daftar:
                    cur.execute(sql, params)
    finally:
        conn.close()


# ---------- Fungsi bantu ----------
def kirim_pesan(jenis, teks):
    """Menyimpan pesan agar tetap tampil setelah halaman dimuat ulang."""
    st.session_state["pesan"] = (jenis, teks)


def tampilkan_pesan():
    if "pesan" in st.session_state:
        jenis, teks = st.session_state.pop("pesan")
        if jenis == "sukses":
            st.success(teks)
        else:
            st.error(teks)


def nomor_berikutnya(tabel, pk, prefix):
    """Mengembalikan nomor urut berikutnya, misalnya kode terbesar PMK005 -> 6."""
    m = query(f"SELECT MAX({pk}) AS m FROM {tabel}")["m"][0]
    return int(m[len(prefix):]) + 1 if m else 1


def id_berikutnya(tabel, pk, prefix):
    """Membuat kode baru, misalnya PMK005 -> PMK006."""
    return f"{prefix}{nomor_berikutnya(tabel, pk, prefix):03d}"


def rupiah(angka):
    return "Rp " + f"{float(angka):,.0f}".replace(",", ".")


def isian(tipe, label, nilai, key):
    """Membuat kotak isian sesuai tipe data."""
    if tipe == "text":
        return st.text_input(label, value="" if nilai is None else str(nilai), key=key)
    if tipe == "int":
        return st.number_input(label, min_value=0, step=1,
                               value=0 if nilai is None else int(nilai), key=key)
    return st.number_input(label, min_value=0.0, step=1000.0,
                           value=0.0 if nilai is None else float(nilai), key=key)


# ---------- Halaman: Dashboard dan Laporan (Fitur 5) ----------
def halaman_dashboard():
    st.title("Dashboard")
    try:
        r = query(
            "SELECT "
            "(SELECT COALESCE(SUM(total_harga), 0) FROM penjualan) AS pendapatan, "
            "(SELECT COALESCE(SUM(jumlah_harga), 0) FROM pembelian) AS nilai_pembelian, "
            "(SELECT COUNT(*) FROM produksi WHERE status = 'Proses') AS batch_proses, "
            "(SELECT COUNT(*) FROM pembelian WHERE status <> 'Selesai') AS pembelian_berjalan"
        ).iloc[0]
    except Exception as e:
        st.error(f"Koneksi database gagal: {e}")
        return

    ambang = st.number_input("Batas stok minimum bahan baku", min_value=0, value=30, step=5,
                             key="ambang_stok")
    menipis = query("SELECT id_bahan, nama_bahan, satuan, stok FROM bahan_baku "
                    "WHERE stok < %s ORDER BY stok, id_bahan", [int(ambang)])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total pendapatan", rupiah(r["pendapatan"]))
    k2.metric("Total nilai pembelian", rupiah(r["nilai_pembelian"]))
    k3.metric("Batch produksi berjalan", int(r["batch_proses"]))
    k4.metric("Bahan stok menipis", len(menipis))

    tab_ringkas, tab_stok, tab_laporan = st.tabs(["Ringkasan", "Stok", "Laporan dan ekspor"])

    # --- Ringkasan ---
    with tab_ringkas:
        st.subheader("Pendapatan per produk")
        df = query("SELECT pr.nama_produk, SUM(pj.jumlah) AS total_unit_terjual, "
                   "SUM(pj.total_harga) AS total_pendapatan "
                   "FROM penjualan pj JOIN produk pr ON pr.id_produk = pj.id_produk "
                   "GROUP BY pr.id_produk, pr.nama_produk ORDER BY total_pendapatan DESC")
        if df.empty:
            st.info("Belum ada penjualan.")
        else:
            df["total_unit_terjual"] = df["total_unit_terjual"].astype(int)
            df["total_pendapatan"] = df["total_pendapatan"].astype(float)
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.bar_chart(df.set_index("nama_produk")["total_pendapatan"])

        st.subheader("Total pembelian per pemasok")
        df = query("SELECT p.nama_pemasok, COUNT(b.id_pembelian) AS jumlah_transaksi, "
                   "SUM(b.jumlah_harga) AS total_pembelian "
                   "FROM pemasok p JOIN pembelian b ON b.id_pemasok = p.id_pemasok "
                   "GROUP BY p.id_pemasok, p.nama_pemasok ORDER BY total_pembelian DESC")
        if df.empty:
            st.info("Belum ada pembelian.")
        else:
            df["jumlah_transaksi"] = df["jumlah_transaksi"].astype(int)
            df["total_pembelian"] = df["total_pembelian"].astype(float)
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.bar_chart(df.set_index("nama_pemasok")["total_pembelian"])

        st.subheader("Produksi per bengkel mitra")
        df = query("SELECT bm.nama_bengkel, COUNT(pd.id_produksi) AS jumlah_batch, "
                   "SUM(pd.jumlah) AS total_unit, SUM(bm.tarif * pd.jumlah) AS estimasi_biaya_jasa "
                   "FROM bengkel_mitra bm JOIN produksi pd ON pd.id_bengkel = bm.id_bengkel "
                   "GROUP BY bm.id_bengkel, bm.nama_bengkel ORDER BY total_unit DESC")
        if df.empty:
            st.info("Belum ada produksi.")
        else:
            df["jumlah_batch"] = df["jumlah_batch"].astype(int)
            df["total_unit"] = df["total_unit"].astype(int)
            df["estimasi_biaya_jasa"] = df["estimasi_biaya_jasa"].astype(float)
            st.dataframe(df, hide_index=True, use_container_width=True)

    # --- Stok ---
    with tab_stok:
        st.subheader(f"Bahan baku dengan stok di bawah {int(ambang)}")
        if menipis.empty:
            st.success("Semua bahan baku masih di atas batas stok minimum.")
        else:
            st.warning(f"{len(menipis)} bahan perlu dibeli. Buat pembelian di menu Pembelian Bahan.")
            st.dataframe(menipis, hide_index=True, use_container_width=True)
        st.subheader("Semua stok bahan baku")
        st.dataframe(query("SELECT id_bahan, nama_bahan, satuan, stok FROM bahan_baku ORDER BY id_bahan"),
                     hide_index=True, use_container_width=True)
        st.subheader("Stok produk jadi")
        st.dataframe(query("SELECT id_produk, nama_produk, stok_produk_jadi FROM produk ORDER BY id_produk"),
                     hide_index=True, use_container_width=True)

    # --- Laporan dan ekspor ---
    with tab_laporan:
        laporan = {
            "Penjualan": "SELECT pj.id_penjualan, pj.tanggal, pr.nama_produk, pj.jumlah, pj.total_harga "
                         "FROM penjualan pj JOIN produk pr ON pr.id_produk = pj.id_produk "
                         "ORDER BY pj.tanggal, pj.id_penjualan",
            "Pembelian": SQL_DAFTAR_PEMBELIAN + " ORDER BY b.tanggal, b.id_pembelian",
            "Rincian pembelian": "SELECT d.id_pembelian, k.nama_bahan, k.satuan, d.jumlah, d.harga_satuan, "
                                 "d.jumlah * d.harga_satuan AS subtotal "
                                 "FROM detail_pembelian d JOIN bahan_baku k ON k.id_bahan = d.id_bahan "
                                 "ORDER BY d.id_pembelian, d.id_detail_pembelian",
            "Produksi": SQL_DAFTAR_PRODUKSI + " ORDER BY pd.tanggal, pd.id_produksi",
            "Stok bahan baku": "SELECT id_bahan, nama_bahan, satuan, stok FROM bahan_baku ORDER BY id_bahan",
            "Stok produk jadi": "SELECT id_produk, nama_produk, harga_jual, stok_produk_jadi "
                                "FROM produk ORDER BY id_produk",
        }
        jenis = st.selectbox("Jenis laporan", list(laporan), key="laporan_jenis")
        df = query(laporan[jenis])
        st.caption(f"{len(df)} baris")
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.download_button(
            "Unduh CSV", df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"laporan_{jenis.lower().replace(' ', '_')}.csv", mime="text/csv",
            key="laporan_unduh",
        )

    with st.expander("Status sistem"):
        jumlah = {t: int(query(f"SELECT COUNT(*) AS n FROM {t}")["n"][0]) for t in TABEL}
        st.success("Terhubung ke database.")
        st.dataframe(pd.DataFrame({"tabel": list(jumlah), "jumlah_baris": list(jumlah.values())}),
                     hide_index=True)


# ---------- Halaman: Data Master (Fitur 1) ----------
def halaman_master():
    st.title("Data Master")
    tampilkan_pesan()

    pilihan = st.radio("Pilih data", list(MASTER.keys()), horizontal=True)
    cfg = MASTER[pilihan]
    t, pk, kolom = cfg["tabel"], cfg["pk"], cfg["kolom"]

    tab_lihat, tab_tambah, tab_ubah, tab_hapus = st.tabs(["Lihat", "Tambah", "Ubah", "Hapus"])

    # --- Lihat ---
    with tab_lihat:
        df = query(f"SELECT * FROM {t} ORDER BY {pk}")
        cari = st.text_input("Cari (nama, kode, dll.)", key=f"cari_{t}")
        if cari:
            cocok = df.astype(str).apply(lambda c: c.str.contains(cari, case=False)).any(axis=1)
            df = df[cocok]
        st.caption(f"{len(df)} data")
        st.dataframe(df, hide_index=True, use_container_width=True)

    # --- Tambah ---
    with tab_tambah:
        with st.form(f"form_tambah_{t}", clear_on_submit=True):
            st.caption("Kode dibuat otomatis oleh sistem.")
            nilai = {k: isian(tipe, label, None, f"tambah_{t}_{k}") for k, label, tipe in kolom}
            simpan = st.form_submit_button("Simpan")
        if simpan:
            if any(tipe == "text" and not str(nilai[k]).strip() for k, _, tipe in kolom):
                st.error("Semua isian teks wajib diisi.")
            else:
                try:
                    kode = id_berikutnya(t, pk, cfg["prefix"])
                    nama_kolom = [pk] + [k for k, _, _ in kolom]
                    isi = [kode] + [nilai[k] for k, _, _ in kolom]
                    marker = ", ".join(["%s"] * len(nama_kolom))
                    run(f"INSERT INTO {t} ({', '.join(nama_kolom)}) VALUES ({marker})", isi)
                    kirim_pesan("sukses", f"Data berhasil ditambahkan dengan kode {kode}.")
                    st.rerun()
                except psycopg2.Error as e:
                    st.error(f"Gagal menyimpan: {e}")

    # --- Ubah ---
    with tab_ubah:
        df = query(f"SELECT * FROM {t} ORDER BY {pk}")
        if df.empty:
            st.info("Belum ada data.")
        else:
            nama = dict(zip(df[pk], df[kolom[0][0]]))
            dipilih = st.selectbox("Pilih data yang diubah", list(df[pk]),
                                   format_func=lambda x: f"{x} - {nama[x]}",
                                   key=f"pilih_ubah_{t}")
            baris = df[df[pk] == dipilih].iloc[0]
            with st.form(f"form_ubah_{t}_{dipilih}"):
                nilai = {k: isian(tipe, label, baris[k], f"ubah_{t}_{dipilih}_{k}")
                         for k, label, tipe in kolom}
                ubah = st.form_submit_button("Simpan perubahan")
            if ubah:
                if any(tipe == "text" and not str(nilai[k]).strip() for k, _, tipe in kolom):
                    st.error("Semua isian teks wajib diisi.")
                else:
                    try:
                        set_sql = ", ".join(f"{k} = %s" for k, _, _ in kolom)
                        isi = [nilai[k] for k, _, _ in kolom] + [dipilih]
                        run(f"UPDATE {t} SET {set_sql} WHERE {pk} = %s", isi)
                        kirim_pesan("sukses", f"Data {dipilih} berhasil diubah.")
                        st.rerun()
                    except psycopg2.Error as e:
                        st.error(f"Gagal mengubah: {e}")

    # --- Hapus ---
    with tab_hapus:
        df = query(f"SELECT * FROM {t} ORDER BY {pk}")
        if df.empty:
            st.info("Belum ada data.")
        else:
            nama = dict(zip(df[pk], df[kolom[0][0]]))
            dipilih = st.selectbox("Pilih data yang dihapus", list(df[pk]),
                                   format_func=lambda x: f"{x} - {nama[x]}",
                                   key=f"pilih_hapus_{t}")
            yakin = st.checkbox("Saya yakin ingin menghapus data ini", key=f"yakin_{t}_{dipilih}")
            if st.button("Hapus", disabled=not yakin, key=f"hapus_{t}"):
                try:
                    run(f"DELETE FROM {t} WHERE {pk} = %s", [dipilih])
                    kirim_pesan("sukses", f"Data {dipilih} berhasil dihapus.")
                    st.rerun()
                except errors.ForeignKeyViolation:
                    st.error(f"Data {dipilih} tidak bisa dihapus karena sudah dipakai di data lain "
                             "(misalnya pembelian, produksi, atau penjualan).")
                except psycopg2.Error as e:
                    st.error(f"Gagal menghapus: {e}")


# ---------- Halaman: Pembelian Bahan (Fitur 2) ----------
# Alur status: Dipesan -> Dikirim -> Selesai (barang diterima, stok bertambah)
ALUR_STATUS = {"Dipesan": "Dikirim", "Dikirim": "Selesai"}

SQL_DAFTAR_PEMBELIAN = """
    SELECT b.id_pembelian, b.tanggal, p.nama_pemasok, b.status, b.jumlah_harga
    FROM pembelian b
    JOIN pemasok p ON p.id_pemasok = b.id_pemasok
"""


def halaman_pembelian():
    st.title("Pembelian Bahan")
    tampilkan_pesan()
    tab_buat, tab_riwayat, tab_status = st.tabs(["Buat pembelian", "Riwayat", "Perbarui status"])

    # --- Buat pembelian ---
    with tab_buat:
        pemasok = query("SELECT id_pemasok, nama_pemasok FROM pemasok ORDER BY id_pemasok")
        bahan = query("SELECT id_bahan, nama_bahan, satuan FROM bahan_baku ORDER BY id_bahan")
        if pemasok.empty or bahan.empty:
            st.info("Isi data pemasok dan bahan baku di menu Data Master terlebih dahulu.")
        else:
            nama_pemasok = dict(zip(pemasok["id_pemasok"], pemasok["nama_pemasok"]))
            c1, c2 = st.columns(2)
            id_pemasok = c1.selectbox("Pemasok", list(nama_pemasok),
                                      format_func=lambda x: f"{x} - {nama_pemasok[x]}",
                                      key="beli_pemasok")
            tanggal = c2.date_input("Tanggal pembelian", value=datetime.date.today(),
                                    key="beli_tanggal")

            st.subheader("Tambah item")
            nama_bahan = dict(zip(bahan["id_bahan"], bahan["nama_bahan"]))
            info_bahan = {r.id_bahan: f"{r.id_bahan} - {r.nama_bahan} ({r.satuan})"
                          for r in bahan.itertuples()}
            c3, c4, c5 = st.columns(3)
            id_bahan = c3.selectbox("Bahan baku", list(info_bahan),
                                    format_func=lambda x: info_bahan[x], key="beli_bahan")
            jumlah = c4.number_input("Jumlah", min_value=1, step=1, value=1, key="beli_jumlah")
            harga = c5.number_input("Harga satuan (Rp)", min_value=0.0, step=1000.0,
                                    key="beli_harga")
            if st.button("Tambah item", key="beli_tambah_item"):
                if harga <= 0:
                    st.error("Harga satuan harus lebih dari 0.")
                else:
                    st.session_state.setdefault("keranjang_beli", []).append({
                        "id_bahan": id_bahan,
                        "nama_bahan": nama_bahan[id_bahan],
                        "jumlah": int(jumlah),
                        "harga_satuan": float(harga),
                    })
                    st.rerun()

            st.subheader("Daftar item")
            keranjang = st.session_state.get("keranjang_beli", [])
            if not keranjang:
                st.caption("Belum ada item.")
            else:
                df = pd.DataFrame(keranjang)
                df["subtotal"] = df["jumlah"] * df["harga_satuan"]
                st.dataframe(df, hide_index=True, use_container_width=True)
                total = float(df["subtotal"].sum())
                st.metric("Total pembelian", rupiah(total))

                b1, b2 = st.columns(2)
                if b1.button("Simpan pembelian", type="primary", key="beli_simpan"):
                    try:
                        id_beli = id_berikutnya("pembelian", "id_pembelian", "PBL")
                        no_detail = nomor_berikutnya("detail_pembelian", "id_detail_pembelian", "DPB")
                        perintah = [(
                            "INSERT INTO pembelian (id_pembelian, tanggal, status, jumlah_harga, id_pemasok) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            [id_beli, tanggal, "Dipesan", total, id_pemasok],
                        )]
                        for i, item in enumerate(keranjang):
                            perintah.append((
                                "INSERT INTO detail_pembelian "
                                "(id_detail_pembelian, jumlah, harga_satuan, id_pembelian, id_bahan) "
                                "VALUES (%s, %s, %s, %s, %s)",
                                [f"DPB{no_detail + i:03d}", item["jumlah"], item["harga_satuan"],
                                 id_beli, item["id_bahan"]],
                            ))
                        run_many(perintah)
                        st.session_state["keranjang_beli"] = []
                        kirim_pesan("sukses", f"Pembelian {id_beli} tersimpan dengan status Dipesan. "
                                              "Stok bertambah setelah status diubah menjadi Selesai.")
                        st.rerun()
                    except psycopg2.Error as e:
                        st.error(f"Gagal menyimpan pembelian: {e}")
                if b2.button("Kosongkan daftar", key="beli_kosongkan"):
                    st.session_state["keranjang_beli"] = []
                    st.rerun()

    # --- Riwayat ---
    with tab_riwayat:
        saring = st.selectbox("Filter status", ["Semua", "Dipesan", "Dikirim", "Selesai"],
                              key="beli_filter")
        sql, params = SQL_DAFTAR_PEMBELIAN, None
        if saring != "Semua":
            sql += " WHERE b.status = %s"
            params = [saring]
        sql += " ORDER BY b.tanggal DESC, b.id_pembelian DESC"
        df = query(sql, params)
        st.dataframe(df, hide_index=True, use_container_width=True)
        if not df.empty:
            pilih = st.selectbox("Lihat rincian pembelian", list(df["id_pembelian"]),
                                 key="beli_rincian")
            rinci = query(
                "SELECT d.id_detail_pembelian, k.nama_bahan, k.satuan, d.jumlah, d.harga_satuan, "
                "d.jumlah * d.harga_satuan AS subtotal "
                "FROM detail_pembelian d JOIN bahan_baku k ON k.id_bahan = d.id_bahan "
                "WHERE d.id_pembelian = %s ORDER BY d.id_detail_pembelian",
                [pilih],
            )
            st.dataframe(rinci, hide_index=True, use_container_width=True)

    # --- Perbarui status ---
    with tab_status:
        df = query(SQL_DAFTAR_PEMBELIAN + " WHERE b.status <> 'Selesai' ORDER BY b.id_pembelian")
        if df.empty:
            st.info("Semua pembelian sudah berstatus Selesai.")
        else:
            info = {r.id_pembelian: f"{r.id_pembelian} - {r.nama_pemasok} ({r.status})"
                    for r in df.itertuples()}
            dipilih = st.selectbox("Pilih pembelian", list(info),
                                   format_func=lambda x: info[x], key="beli_status_pilih")
            sekarang = df.loc[df["id_pembelian"] == dipilih, "status"].iloc[0]
            baru = ALUR_STATUS.get(sekarang)
            if baru is None:
                st.error(f"Status '{sekarang}' tidak dikenali.")
            else:
                st.write(f"Status saat ini: **{sekarang}**  ->  status berikutnya: **{baru}**")
                if baru == "Selesai":
                    st.warning("Stok bahan baku akan bertambah sesuai rincian pembelian. "
                               "Status Selesai tidak bisa diubah lagi.")
                if st.button(f"Ubah status menjadi {baru}", key="beli_ubah_status"):
                    try:
                        terkini = query("SELECT status FROM pembelian WHERE id_pembelian = %s",
                                        [dipilih])["status"][0]
                        if terkini != sekarang:
                            st.error("Status pembelian ini sudah berubah. Muat ulang halaman.")
                        else:
                            perintah = [("UPDATE pembelian SET status = %s WHERE id_pembelian = %s",
                                         [baru, dipilih])]
                            if baru == "Selesai":
                                rincian = query("SELECT id_bahan, jumlah FROM detail_pembelian "
                                                "WHERE id_pembelian = %s", [dipilih])
                                for r in rincian.itertuples():
                                    perintah.append((
                                        "UPDATE bahan_baku SET stok = stok + %s WHERE id_bahan = %s",
                                        [int(r.jumlah), r.id_bahan],
                                    ))
                            run_many(perintah)
                            kirim_pesan("sukses", f"Status {dipilih} diubah menjadi {baru}."
                                        + (" Stok bahan baku sudah diperbarui." if baru == "Selesai" else ""))
                            st.rerun()
                    except psycopg2.Error as e:
                        st.error(f"Gagal mengubah status: {e}")


# ---------- Halaman: Produksi (Fitur 3) ----------
# Alur: batch dibuat (status Proses) -> bahan baku langsung dikurangi (dikirim ke bengkel)
#       -> batch ditandai Selesai -> stok produk jadi bertambah.
SQL_DAFTAR_PRODUKSI = """
    SELECT pd.id_produksi, pd.tanggal, pr.nama_produk, bm.nama_bengkel,
           pd.jumlah, pd.status, bm.tarif * pd.jumlah AS estimasi_biaya_jasa
    FROM produksi pd
    JOIN produk pr ON pr.id_produk = pd.id_produk
    JOIN bengkel_mitra bm ON bm.id_bengkel = pd.id_bengkel
"""


def halaman_produksi():
    st.title("Produksi")
    tampilkan_pesan()
    tab_buat, tab_riwayat, tab_status = st.tabs(["Buat batch produksi", "Riwayat", "Perbarui status"])

    # --- Buat batch produksi ---
    with tab_buat:
        produk = query("SELECT id_produk, nama_produk FROM produk ORDER BY id_produk")
        bengkel = query("SELECT id_bengkel, nama_bengkel, tarif FROM bengkel_mitra ORDER BY id_bengkel")
        if produk.empty or bengkel.empty:
            st.info("Isi data produk dan bengkel mitra di menu Data Master terlebih dahulu.")
        else:
            nama_produk = dict(zip(produk["id_produk"], produk["nama_produk"]))
            nama_bengkel = dict(zip(bengkel["id_bengkel"], bengkel["nama_bengkel"]))
            tarif_bengkel = dict(zip(bengkel["id_bengkel"], bengkel["tarif"]))

            c1, c2 = st.columns(2)
            id_produk = c1.selectbox("Produk yang dibuat", list(nama_produk),
                                     format_func=lambda x: f"{x} - {nama_produk[x]}",
                                     key="prod_produk")
            id_bengkel = c2.selectbox("Bengkel mitra", list(nama_bengkel),
                                      format_func=lambda x: f"{x} - {nama_bengkel[x]}",
                                      key="prod_bengkel")
            c3, c4 = st.columns(2)
            jumlah = c3.number_input("Jumlah unit dalam batch", min_value=1, step=1, value=5,
                                     key="prod_jumlah")
            tanggal = c4.date_input("Tanggal produksi", value=datetime.date.today(),
                                    key="prod_tanggal")

            bom = query(
                "SELECT k.id_bahan, k.nama_bahan, k.satuan, bm.jumlah_per_unit, k.stok "
                "FROM bill_of_material bm JOIN bahan_baku k ON k.id_bahan = bm.id_bahan "
                "WHERE bm.id_produk = %s ORDER BY k.id_bahan",
                [id_produk],
            )
            if bom.empty:
                st.error("Produk ini belum punya Bill of Material (resep bahan).")
            else:
                baris, kurang = [], False
                for r in bom.itertuples():
                    perlu = math.ceil(r.jumlah_per_unit * int(jumlah))
                    cukup = int(r.stok) >= perlu
                    kurang = kurang or not cukup
                    baris.append({
                        "id_bahan": r.id_bahan, "nama_bahan": r.nama_bahan, "satuan": r.satuan,
                        "per_unit": float(r.jumlah_per_unit), "kebutuhan": perlu,
                        "stok": int(r.stok), "status": "Cukup" if cukup else "KURANG",
                    })
                st.subheader("Kebutuhan bahan")
                st.caption("Kebutuhan = jumlah per unit x jumlah unit, dibulatkan ke atas.")
                st.dataframe(pd.DataFrame(baris), hide_index=True, use_container_width=True)
                st.write("Estimasi biaya jasa bengkel (tarif x jumlah unit): "
                         f"**{rupiah(float(tarif_bengkel[id_bengkel]) * int(jumlah))}**")
                if kurang:
                    st.error("Stok bahan tidak cukup. Lakukan pembelian bahan dulu (menu Pembelian Bahan) "
                             "atau kurangi jumlah unit.")

                if st.button("Mulai produksi", type="primary", disabled=kurang, key="prod_mulai"):
                    try:
                        id_prod = id_berikutnya("produksi", "id_produksi", "PRK")
                        perintah = [(
                            "INSERT INTO produksi (id_produksi, tanggal, jumlah, status, id_produk, id_bengkel) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            [id_prod, tanggal, int(jumlah), "Proses", id_produk, id_bengkel],
                        )]
                        for b in baris:
                            perintah.append((
                                "UPDATE bahan_baku SET stok = stok - %s WHERE id_bahan = %s",
                                [b["kebutuhan"], b["id_bahan"]],
                            ))
                        run_many(perintah)
                        kirim_pesan("sukses", f"Batch {id_prod} dimulai (status Proses). "
                                              "Stok bahan baku sudah dikurangi.")
                        st.rerun()
                    except errors.CheckViolation:
                        st.error("Stok bahan tidak cukup (stok baru saja berubah). Muat ulang halaman.")
                    except psycopg2.Error as e:
                        st.error(f"Gagal memulai produksi: {e}")

    # --- Riwayat ---
    with tab_riwayat:
        saring = st.selectbox("Filter status", ["Semua", "Proses", "Selesai"], key="prod_filter")
        sql, params = SQL_DAFTAR_PRODUKSI, None
        if saring != "Semua":
            sql += " WHERE pd.status = %s"
            params = [saring]
        sql += " ORDER BY pd.tanggal DESC, pd.id_produksi DESC"
        df = query(sql, params)
        st.dataframe(df, hide_index=True, use_container_width=True)
        if not df.empty:
            pilih = st.selectbox("Lihat kebutuhan bahan batch", list(df["id_produksi"]),
                                 key="prod_rincian")
            rinci = query(
                "SELECT k.nama_bahan, k.satuan, bm.jumlah_per_unit, "
                "bm.jumlah_per_unit * pd.jumlah AS kebutuhan "
                "FROM produksi pd "
                "JOIN bill_of_material bm ON bm.id_produk = pd.id_produk "
                "JOIN bahan_baku k ON k.id_bahan = bm.id_bahan "
                "WHERE pd.id_produksi = %s ORDER BY k.nama_bahan",
                [pilih],
            )
            st.dataframe(rinci, hide_index=True, use_container_width=True)

    # --- Perbarui status ---
    with tab_status:
        df = query(SQL_DAFTAR_PRODUKSI + " WHERE pd.status = 'Proses' ORDER BY pd.id_produksi")
        if df.empty:
            st.info("Tidak ada batch yang sedang diproses.")
        else:
            info = {r.id_produksi: f"{r.id_produksi} - {r.nama_produk} x{r.jumlah} ({r.nama_bengkel})"
                    for r in df.itertuples()}
            dipilih = st.selectbox("Pilih batch", list(info), format_func=lambda x: info[x],
                                   key="prod_status_pilih")
            st.warning("Menandai Selesai akan menambah stok produk jadi sesuai jumlah batch. "
                       "Status Selesai tidak bisa diubah lagi.")
            if st.button("Tandai Selesai", key="prod_selesai"):
                try:
                    terkini = query("SELECT status, id_produk, jumlah FROM produksi "
                                    "WHERE id_produksi = %s", [dipilih]).iloc[0]
                    if terkini["status"] != "Proses":
                        st.error("Status batch ini sudah berubah. Muat ulang halaman.")
                    else:
                        run_many([
                            ("UPDATE produksi SET status = 'Selesai' WHERE id_produksi = %s", [dipilih]),
                            ("UPDATE produk SET stok_produk_jadi = stok_produk_jadi + %s "
                             "WHERE id_produk = %s", [int(terkini["jumlah"]), terkini["id_produk"]]),
                        ])
                        kirim_pesan("sukses", f"Batch {dipilih} selesai. Stok produk jadi sudah bertambah.")
                        st.rerun()
                except psycopg2.Error as e:
                    st.error(f"Gagal mengubah status: {e}")


# ---------- Halaman: Penjualan (Fitur 4) ----------
def halaman_penjualan():
    st.title("Penjualan")
    tampilkan_pesan()
    tab_catat, tab_riwayat, tab_rekap = st.tabs(["Catat penjualan", "Riwayat", "Rekap per produk"])

    produk = query("SELECT id_produk, nama_produk, harga_jual, stok_produk_jadi "
                   "FROM produk ORDER BY id_produk")
    nama_produk = dict(zip(produk["id_produk"], produk["nama_produk"]))

    # --- Catat penjualan ---
    with tab_catat:
        if produk.empty:
            st.info("Isi data produk di menu Data Master terlebih dahulu.")
        else:
            info = {r.id_produk: f"{r.id_produk} - {r.nama_produk} (stok {r.stok_produk_jadi})"
                    for r in produk.itertuples()}
            id_produk = st.selectbox("Produk", list(info), format_func=lambda x: info[x],
                                     key="jual_produk")
            baris = produk[produk["id_produk"] == id_produk].iloc[0]
            stok, harga = int(baris["stok_produk_jadi"]), float(baris["harga_jual"])

            c1, c2 = st.columns(2)
            jumlah = c1.number_input("Jumlah terjual", min_value=1, step=1, value=1, key="jual_jumlah")
            tanggal = c2.date_input("Tanggal penjualan", value=datetime.date.today(),
                                    key="jual_tanggal")
            total = harga * int(jumlah)
            st.write(f"Harga satuan: **{rupiah(harga)}**  |  Stok tersedia: **{stok} unit**")
            st.metric("Total penjualan", rupiah(total))

            kurang = int(jumlah) > stok
            if kurang:
                st.error(f"Stok tidak cukup. Stok tersedia hanya {stok} unit.")

            if st.button("Simpan penjualan", type="primary", disabled=kurang, key="jual_simpan"):
                try:
                    id_jual = id_berikutnya("penjualan", "id_penjualan", "PJL")
                    run_many([
                        ("INSERT INTO penjualan (id_penjualan, tanggal, jumlah, total_harga, id_produk) "
                         "VALUES (%s, %s, %s, %s, %s)",
                         [id_jual, tanggal, int(jumlah), total, id_produk]),
                        ("UPDATE produk SET stok_produk_jadi = stok_produk_jadi - %s "
                         "WHERE id_produk = %s", [int(jumlah), id_produk]),
                    ])
                    kirim_pesan("sukses", f"Penjualan {id_jual} tersimpan. Total {rupiah(total)}. "
                                          "Stok produk jadi sudah dikurangi.")
                    st.rerun()
                except errors.CheckViolation:
                    st.error("Stok tidak cukup (stok baru saja berubah). Muat ulang halaman.")
                except psycopg2.Error as e:
                    st.error(f"Gagal menyimpan penjualan: {e}")

    # --- Riwayat ---
    with tab_riwayat:
        pilihan = ["Semua"] + list(produk["id_produk"])
        saring = st.selectbox("Filter produk", pilihan,
                              format_func=lambda x: "Semua produk" if x == "Semua"
                              else f"{x} - {nama_produk[x]}",
                              key="jual_filter")
        sql = ("SELECT pj.id_penjualan, pj.tanggal, pr.nama_produk, pj.jumlah, pj.total_harga "
               "FROM penjualan pj JOIN produk pr ON pr.id_produk = pj.id_produk")
        params = None
        if saring != "Semua":
            sql += " WHERE pj.id_produk = %s"
            params = [saring]
        sql += " ORDER BY pj.tanggal DESC, pj.id_penjualan DESC"
        df = query(sql, params)
        pendapatan = float(df["total_harga"].sum()) if not df.empty else 0.0
        st.metric("Total pendapatan (sesuai filter)", rupiah(pendapatan))
        st.dataframe(df, hide_index=True, use_container_width=True)

    # --- Rekap per produk ---
    with tab_rekap:
        df = query(
            "SELECT pr.nama_produk, SUM(pj.jumlah) AS total_unit_terjual, "
            "SUM(pj.total_harga) AS total_pendapatan "
            "FROM penjualan pj JOIN produk pr ON pr.id_produk = pj.id_produk "
            "GROUP BY pr.id_produk, pr.nama_produk ORDER BY total_pendapatan DESC"
        )
        if df.empty:
            st.info("Belum ada penjualan.")
        else:
            df["total_unit_terjual"] = df["total_unit_terjual"].astype(int)
            df["total_pendapatan"] = df["total_pendapatan"].astype(float)
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.bar_chart(df.set_index("nama_produk")["total_pendapatan"])


def halaman_kosong(nama):
    st.title(nama)
    st.info("Halaman ini akan dibuat pada tahap berikutnya.")


# ---------- Menu ----------
MENU = {
    "Dashboard": halaman_dashboard,
    "Data Master": halaman_master,
    "Pembelian Bahan": halaman_pembelian,
    "Produksi": halaman_produksi,
    "Penjualan": halaman_penjualan,
}

st.sidebar.title("Menu")
pilihan_menu = st.sidebar.radio("Pilih halaman", list(MENU.keys()))
MENU[pilihan_menu]()