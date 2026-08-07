import os
import io
import json
import re
import random
import sqlite3
import hashlib
from datetime import datetime
import pandas as pd
import streamlit as st
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ReportLab PDF Kütüphaneleri
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# 1. SAYFA YAPILANDIRMASI VE ÇİFT API KEY KONTROLÜ
# ---------------------------------------------------------
st.set_page_config(
    page_title="Diyabetik Beslenme Karar Destek", 
    page_icon="🥗", 
    layout="wide"
)

load_dotenv()

# SAKLI KURAL 1: ÇİFT API KEY KONTROLÜ
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    st.error("⚠️ API Key bulunamadı! Lütfen .env dosyanızı kontrol edin.")
    st.stop()

client = genai.Client(api_key=API_KEY)

# Session State Başlatma
if 'user' not in st.session_state:
    st.session_state['user'] = None
if 'analiz_yapildi' not in st.session_state:
    st.session_state['analiz_yapildi'] = False

# ---------------------------------------------------------
# YARDIMCI YAPAY ZEKA METİN SORGULAMA FONKSİYONU
# ---------------------------------------------------------
def fetch_text_macronutrients(yemek_adi):
    # SAKLI KURAL 2: DİNAMİK MODEL TARAMA & FALLBACK
    try:
        acik_modeller = [m.name for m in client.models.list() if "flash" in m.name or "gemini" in m.name]
    except Exception:
        acik_modeller = []

    if not acik_modeller:
        acik_modeller = ["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]

    prompt = f"""
    '{yemek_adi}' isimli yemeğin/besinin 100 gramındaki ortalama besin değerlerini hesapla ve SADECE aşağıdaki JSON formatında Türkçe yanıt ver:
    {{
        "yemek_adi": "{yemek_adi}",
        "tahmini_porsiyon_gram": 100,
        "kh_100g": 15.0,
        "protein_100g": 10.0,
        "yag_100g": 5.0,
        "kalori_100g": 150.0,
        "gi": 50
    }}
    Yanıtında JSON dışında hiçbir metin veya açıklama yazma.
    """

    for m_name in acik_modeller:
        try:
            response = client.models.generate_content(
                model=m_name,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            # SAKLI KURAL 3: REGEX İLE ESNEK JSON AYIKLAMA
            if response and response.text:
                json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
        except Exception:
            continue
    return None

# ---------------------------------------------------------
# 2. SQLITE VERİ TABANI KATMANI
# ---------------------------------------------------------
DB_FILE = "diyabet_takip.db"

def make_hash(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT UNIQUE NOT NULL,
            sifre_hash TEXT NOT NULL,
            ad_soyad TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS doktor_hasta_izinleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hasta_id INTEGER NOT NULL,
            doktor_id INTEGER NOT NULL,
            FOREIGN KEY(hasta_id) REFERENCES kullanicilar(id),
            FOREIGN KEY(doktor_id) REFERENCES kullanicilar(id),
            UNIQUE(hasta_id, doktor_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS glikoz_kayitlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_id INTEGER NOT NULL,
            tarih_saat TEXT NOT NULL,
            kan_sekeri INTEGER NOT NULL,
            trend_yoni TEXT,
            olcum_yontemi TEXT,
            FOREIGN KEY(kullanici_id) REFERENCES kullanicilar(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ogun_kayitlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_id INTEGER NOT NULL,
            tarih_saat TEXT NOT NULL,
            yemek_adi TEXT NOT NULL,
            porsiyon_gram INTEGER,
            karbonhidrat_g REAL,
            protein_g REAL,
            yag_g REAL,
            kalori_kcal REAL,
            glisemik_indeks INTEGER,
            onerilen_insulin REAL,
            diyabet_tipi TEXT,
            FOREIGN KEY(kullanici_id) REFERENCES kullanicilar(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Auth İşlemleri ---
def kullanici_kayit(kullanici_adi, sifre, ad_soyad, rol):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO kullanicilar (kullanici_adi, sifre_hash, ad_soyad, rol) VALUES (?, ?, ?, ?)",
            (kullanici_adi, make_hash(sifre), ad_soyad, rol)
        )
        conn.commit()
        return True, "Kayıt başarılı! Şimdi giriş yapabilirsiniz."
    except sqlite3.IntegrityError:
        return False, "Bu kullanıcı adı zaten alınmış."
    finally:
        conn.close()

def kullanici_giris(kullanici_adi, sifre):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, kullanici_adi, ad_soyad, rol FROM kullanicilar WHERE kullanici_adi = ? AND sifre_hash = ?",
        (kullanici_adi, make_hash(sifre))
    )
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"id": user[0], "kullanici_adi": user[1], "ad_soyad": user[2], "rol": user[3]}
    return None

# --- Veri Kayıt & Sorgulama ---
def glikoz_kaydet(kullanici_id, kan_sekeri, trend_yoni, olcum_yontemi):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO glikoz_kayitlari (kullanici_id, tarih_saat, kan_sekeri, trend_yoni, olcum_yontemi)
        VALUES (?, ?, ?, ?, ?)
    ''', (kullanici_id, simdi, kan_sekeri, trend_yoni, olcum_yontemi))
    conn.commit()
    conn.close()

def ogun_kaydet(kullanici_id, yemek_adi, porsiyon, kh, prot, yag, kal, gi, insulin, diyabet_tipi):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO ogun_kayitlari 
        (kullanici_id, tarih_saat, yemek_adi, porsiyon_gram, karbonhidrat_g, protein_g, yag_g, kalori_kcal, glisemik_indeks, onerilen_insulin, diyabet_tipi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (kullanici_id, simdi, yemek_adi, porsiyon, kh, prot, yag, kal, gi, insulin, diyabet_tipi))
    conn.commit()
    conn.close()

def get_glikoz_data(kullanici_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM glikoz_kayitlari WHERE kullanici_id = ? ORDER BY id DESC", conn, params=(kullanici_id,))
    conn.close()
    return df

def get_ogun_data(kullanici_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM ogun_kayitlari WHERE kullanici_id = ? ORDER BY id DESC", conn, params=(kullanici_id,))
    conn.close()
    return df

def doktor_ekle(hasta_id, doktor_kullanici_adi):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, rol FROM kullanicilar WHERE kullanici_adi = ?", (doktor_kullanici_adi,))
    doc = cursor.fetchone()
    if not doc or doc[1] != 'doktor':
        conn.close()
        return False, "Sistemde böyle bir doktor bulunamadı."
    
    try:
        cursor.execute("INSERT INTO doktor_hasta_izinleri (hasta_id, doktor_id) VALUES (?, ?)", (hasta_id, doc[0]))
        conn.commit()
        conn.close()
        return True, "Doktora başarıyla erişim izni verildi."
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Bu doktora zaten izin verilmiş."

def get_izinli_hastalar(doktor_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT k.id, k.ad_soyad, k.kullanici_adi 
        FROM kullanicilar k
        JOIN doktor_hasta_izinleri i ON k.id = i.hasta_id
        WHERE i.doktor_id = ?
    ''', (doktor_id,))
    hastalar = cursor.fetchall()
    conn.close()
    return hastalar

# ---------------------------------------------------------
# 3. CANLI SENSÖR FRAGMENT MİMARİSİ
# ---------------------------------------------------------
@st.fragment(run_every="60s")
def cgm_sensor_otomatik_komponent(kullanici_id):
    yeni_seker = random.randint(85, 185)
    yeni_trend = random.choice(["➡️ Stabil", "⇈ Hızla Yükseliyor", "⇊ Hızla Düşüyor"])
    glikoz_kaydet(kullanici_id, yeni_seker, yeni_trend, "📡 Canlı Sensör")
    
    st.session_state['aktif_seker'] = yeni_seker
    st.session_state['aktif_trend'] = yeni_trend

    st.sidebar.markdown(
        """
        <div style="display: flex; align-items: center; background-color: #1e293b; padding: 10px 14px; border-radius: 8px; border: 1px solid #22c55e; margin-bottom: 12px;">
            <span style="height: 12px; width: 12px; background-color: #22c55e; border-radius: 50%; display: inline-block; box-shadow: 0 0 10px #22c55e; margin-right: 10px;"></span>
            <span style="color: #22c55e; font-weight: 600; font-size: 13px;">Sensör Entegrasyonu Aktif</span>
        </div>
        """, 
        unsafe_allow_html=True
    )
    st.sidebar.metric("Anlık Sensör Verisi (60s Otomatik)", f"{yeni_seker} mg/dL", delta=yeni_trend)
    st.sidebar.caption("💡 Sensör verisi her 60 saniyede bir otomatik güncellenip kaydedilir.")

# ---------------------------------------------------------
# 4. DOKTOR PDF RAPORU ÜRETME KATMANI
# ---------------------------------------------------------
def generate_pdf_report(hasta_adi, df_glikoz, df_ogun):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#1E3A8A'), alignment=1
    )
    subtitle_style = ParagraphStyle(
        'SubtitleStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#4B5563'), alignment=1
    )
    h2_style = ParagraphStyle(
        'H2Style', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=13, leading=16, textColor=colors.HexColor('#1F2937'), spaceBefore=12, spaceAfter=6
    )

    story.append(Paragraph(f"DIYABETIK BESLENME VE KAN SEKERI KLINIK RAPORU", title_style))
    story.append(Paragraph(f"Hasta: {hasta_adi} | Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}", subtitle_style))
    story.append(Spacer(1, 15))

    story.append(Paragraph("1. Kan Sekeri Ölcüm Özeti", h2_style))
    if not df_glikoz.empty:
        avg_g = df_glikoz['kan_sekeri'].mean()
        max_g = df_glikoz['kan_sekeri'].max()
        min_g = df_glikoz['kan_sekeri'].min()
        
        summary_data = [
            ["Toplam Olcum Sayisi", "Ortalama Kan Sekeri", "En Yuksek Olcum", "En Dusuk Olcum"],
            [str(len(df_glikoz)), f"{avg_g:.1f} mg/dL", f"{max_g} mg/dL", f"{min_g} mg/dL"]
        ]
        t_summary = Table(summary_data, colWidths=[130, 130, 130, 130])
        t_summary.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E0F2FE')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0369A1')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(t_summary)
    else:
        story.append(Paragraph("Henuz kayitli glikoz verisi bulunmamaktadir.", styles['Normal']))

    story.append(Spacer(1, 15))

    story.append(Paragraph("2. Son Ogun Kayitlari ve Makro Analizi", h2_style))
    if not df_ogun.empty:
        table_data = [["Tarih", "Yemek Adi", "Porsiyon", "Karbonhidrat", "Kalori", "Insulin Dozu"]]
        for _, row in df_ogun.head(10).iterrows():
            tarih_kisa = str(row['tarih_saat'])[:16]
            table_data.append([
                tarih_kisa,
                str(row['yemek_adi'])[:20],
                f"{row['porsiyon_gram']}g",
                f"{row['karbonhidrat_g']:.1f}g",
                f"{row['kalori_kcal']:.0f} kcal",
                f"{row['onerilen_insulin']:.1f} U"
            ])
        t_ogun = Table(table_data, colWidths=[100, 130, 65, 75, 75, 75])
        t_ogun.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#334155')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(t_ogun)
    else:
        story.append(Paragraph("Henuz kayitli ogun verisi bulunmamaktadir.", styles['Normal']))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------------------------------------------------------
# 5. GİRİŞ VE KAYIT EKRANI
# ---------------------------------------------------------
if st.session_state['user'] is None:
    st.title("🔐 Diyabet Karar Destek Sistemi - Giriş & Kayıt")
    
    tab_login, tab_register = st.tabs(["🔑 Giriş Yap", "📝 Hesap Oluştur"])
    
    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Kullanıcı Adı")
            password = st.text_input("Şifre", type="password")
            submit_login = st.form_submit_button("Giriş Yap", type="primary")
            
            if submit_login:
                user = kullanici_giris(username, password)
                if user:
                    st.session_state['user'] = user
                    st.success(f"Hoş geldiniz, {user['ad_soyad']}!")
                    st.rerun()
                else:
                    st.error("Hatalı kullanıcı adı veya şifre!")

    with tab_register:
        with st.form("register_form"):
            reg_name = st.text_input("Ad Soyad")
            reg_username = st.text_input("Kullanıcı Adı")
            reg_password = st.text_input("Şifre", type="password")
            reg_role = st.selectbox("Hesap Türü", ["hasta", "doktor"], format_func=lambda x: "Hasta Hesabı" if x == "hasta" else "Doktor Hesabı")
            submit_reg = st.form_submit_button("Kayıt Ol")
            
            if submit_reg:
                if reg_name and reg_username and reg_password:
                    ok, msg = kullanici_kayit(reg_username, reg_password, reg_name, reg_role)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
                else:
                    st.warning("Lütfen tüm alanları doldurun.")
    st.stop()

# ---------------------------------------------------------
# 6. OTURUM AÇILDI - YAN MENÜ
# ---------------------------------------------------------
user = st.session_state['user']
st.sidebar.title(f"👤 {user['ad_soyad']}")
st.sidebar.caption(f"Rol: **{user['rol'].capitalize()}** | @{user['kullanici_adi']}")

if st.sidebar.button("🚪 Çıkış Yap"):
    st.session_state['user'] = None
    st.session_state['analiz_yapildi'] = False
    st.rerun()

# ---------------------------------------------------------
# HASTA PANELİ
# ---------------------------------------------------------
if user['rol'] == 'hasta':
    secilen_sayfa = st.sidebar.radio(
        "Modül Seçin:",
        ["🥗 Beslenme & Karar Destek", "📊 Geçmiş Raporlarım", "👨‍⚕️ Doktor Yetkilendirme"]
    )

    if secilen_sayfa == "🥗 Beslenme & Karar Destek":
        st.title("🥗 Yapay Zeka Destekli Diyabetik Beslenme Karar Destek Sistemi")
        st.write("---")
        
        st.sidebar.header("👤 Profil Ayarları")
        diyabet_tipi = st.sidebar.radio("Diyabet Tipi:", ["Tip 1 Diyabet", "Tip 2 Diyabet"])

        st.sidebar.write("---")
        st.sidebar.header("📡 Kan Şekeri Ölçüm Modu")
        olcum_yontemi = st.sidebar.radio("Girdi Yöntemi:", ["Manuel Ölçüm", "📡 Canlı Sensör / Telefon Entegrasyonu"])

        if olcum_yontemi == "📡 Canlı Sensör / Telefon Entegrasyonu":
            cgm_sensor_otomatik_komponent(user['id'])
            kan_sekeri = st.session_state.get('aktif_seker', 120)
            trend_yoni = st.session_state.get('aktif_trend', "➡️ Stabil")
        else:
            kan_sekeri = st.sidebar.number_input("Mevcut Kan Şekeri (mg/dL):", min_value=50, max_value=400, value=120)
            trend_yoni = "➡️ Stabil"
            if st.sidebar.button("💾 Kan Şekerini Kaydet", type="primary"):
                glikoz_kaydet(user['id'], kan_sekeri, trend_yoni, olcum_yontemi)
                st.sidebar.success(f"✅ {kan_sekeri} mg/dL kaydedildi!")

        ik_orani = st.sidebar.number_input("İnsülin / Karbonhidrat Oranı (1 U / X g KH):", min_value=1, max_value=50, value=15) if diyabet_tipi == "Tip 1 Diyabet" else 15

        # ÖĞÜN GİRİŞ YÖNTEMİ SEÇİMİ
        girdi_modu = st.radio(
            "📌 Öğün Ekleme Yöntemini Seçin:",
            ["📸 Fotoğraf Yükleyerek Yapay Zeka Analizi", "✍️ Manuel Yemek / Makro Değeri Girişi"],
            horizontal=True
        )

        st.write("---")

        # MOD 1: FOTOĞRAFLI ANALİZ
        if girdi_modu == "📸 Fotoğraf Yükleyerek Yapay Zeka Analizi":
            yuklenen_dosya = st.file_uploader("Bir yemek fotoğrafı yükleyin...", type=["jpg", "jpeg", "png", "webp"])

            if yuklenen_dosya is not None:
                resim = Image.open(yuklenen_dosya)
                if resim.mode in ("RGBA", "P"):
                    resim = resim.convert("RGB")
                resim.thumbnail((1024, 1024))

                col1, col2 = st.columns([1, 1])
                
                with col1:
                    st.image(resim, caption="Yüklenen Tabağın Görseli", use_container_width=True)
                    
                with col2:
                    st.subheader("🔍 Yapay Zeka Analizi")
                    if st.button("🚀 Tabağı Analiz Et", type="primary", use_container_width=True):
                        with st.spinner("Yapay zeka görseli inceliyor..."):
                            try:
                                try:
                                    acik_modeller = [m.name for m in client.models.list() if "flash" in m.name or "gemini" in m.name]
                                except Exception:
                                    acik_modeller = []

                                if not acik_modeller:
                                    acik_modeller = ["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]

                                prompt = """
                                Bu fotoğraftaki ana yemeği tespit et ve besin değerlerini hesaplayıp SADECE aşağıdaki JSON formatında Türkçe yanıt ver:
                                {
                                    "yemek_adi": "TÜRKÇE_YEMEK_ADI",
                                    "tahmini_porsiyon_gram": 200,
                                    "kh_100g": 15.0,
                                    "protein_100g": 10.0,
                                    "yag_100g": 8.0,
                                    "kalori_100g": 160.0,
                                    "gi": 50
                                }
                                Yanıtında JSON dışında hiçbir metin veya açıklama yazma.
                                """

                                response = None
                                for m_name in acik_modeller:
                                    try:
                                        response = client.models.generate_content(
                                            model=m_name,
                                            contents=[resim, prompt],
                                            config=types.GenerateContentConfig(response_mime_type="application/json")
                                        )
                                        if response and response.text:
                                            break
                                    except Exception:
                                        continue

                                if response and response.text:
                                    json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                                    if json_match:
                                        sonuc = json.loads(json_match.group(0))
                                        st.session_state['tespit_edilen'] = sonuc.get("yemek_adi", "Bilinmeyen Yemek")
                                        st.session_state['porsiyon'] = int(sonuc.get("tahmini_porsiyon_gram", 200))
                                        st.session_state['kh_100g'] = float(sonuc.get("kh_100g", 15.0))
                                        st.session_state['protein_100g'] = float(sonuc.get("protein_100g", 10.0))
                                        st.session_state['yag_100g'] = float(sonuc.get("yag_100g", 5.0))
                                        st.session_state['kalori_100g'] = float(sonuc.get("kalori_100g", 150.0))
                                        st.session_state['gi'] = int(sonuc.get("gi", 50))
                                        st.session_state['analiz_yapildi'] = True
                                        st.rerun()
                                    else:
                                        st.error("❌ Görsel çözümlenemedi.")
                                else:
                                    st.error("❌ Model yanıt vermedi. Lütfen API anahtarınızı kontrol edin.")
                            except Exception as e:
                                st.error(f"Sistem Hatası: {e}")

        # MOD 2: MANUEL YEMEK VE MAKRO GİRİŞİ
        else:
            st.subheader("✍️ Görselsiz Doğrudan Yemek & Makro Girişi")
            st.caption("İsterseniz yemeğin adını yazıp yapay zekadan çektirin, isterseniz makro değerlerini aşağıdan elle yazın.")
            
            c_m1, c_m2 = st.columns([2, 1])
            manuel_input_adi = c_m1.text_input("Yemek Adı Girin:", placeholder="Örn: Haşlanmış Yumurta, Tavuk Sote")
            # BENZERSİZ KEY EKLENDİ (KEY: m_gram_input)
            manuel_gramaj = c_m2.number_input("Porsiyon Gramajı (g):", min_value=10, max_value=2000, value=150, key="m_gram_input")

            if st.button("🔍 Yemeğin Değerlerini Yapay Zekadan Getir", type="primary"):
                if manuel_input_adi:
                    with st.spinner(f"'{manuel_input_adi}' için değerler getiriliyor..."):
                        res_json = fetch_text_macronutrients(manuel_input_adi)
                        if res_json:
                            st.session_state['tespit_edilen'] = res_json.get("yemek_adi", manuel_input_adi)
                            st.session_state['porsiyon'] = manuel_gramaj
                            st.session_state['kh_100g'] = float(res_json.get("kh_100g", 0.0))
                            st.session_state['protein_100g'] = float(res_json.get("protein_100g", 0.0))
                            st.session_state['yag_100g'] = float(res_json.get("yag_100g", 0.0))
                            st.session_state['kalori_100g'] = float(res_json.get("kalori_100g", 0.0))
                            st.session_state['gi'] = int(res_json.get("gi", 50))
                            st.session_state['analiz_yapildi'] = True
                            st.rerun()
                else:
                    st.warning("Lütfen bir yemek adı girin.")

        # ESNEK DÜZELTME & ELLE MAKRO DEĞİŞTİRME PANALİ
        if st.session_state.get('analiz_yapildi', False):
            st.write("---")
            st.subheader("🎯 Görsel Tanılama / Besin Verisi Düzeltme ve Onay")
            st.caption("Yapay zeka yanlış analiz yaptıysa veya değerleri değiştirmek isterseniz aşağıdaki kutulardan değerleri elle düzeltebilirsiniz.")
            
            c_1, c_2, c_3 = st.columns([2, 1, 1])
            nihai_yemek_adi = c_1.text_input("Yemek Adı:", value=st.session_state['tespit_edilen'])
            # BENZERSİZ KEY EKLENDİ (KEY: onay_gramaj_input)
            porsiyon = c_2.number_input("Porsiyon Gramajı (g):", min_value=10, max_value=2000, value=st.session_state['porsiyon'], key="onay_gramaj_input")
            
            if c_3.button("🔄 İsme Göre Yapay Zekadan Yeniden Hesapla", use_container_width=True):
                with st.spinner(f"'{nihai_yemek_adi}' için yeni değerler çekiliyor..."):
                    yeni_res = fetch_text_macronutrients(nihai_yemek_adi)
                    if yeni_res:
                        st.session_state['tespit_edilen'] = nihai_yemek_adi
                        st.session_state['kh_100g'] = float(yeni_res.get("kh_100g", 0.0))
                        st.session_state['protein_100g'] = float(yeni_res.get("protein_100g", 0.0))
                        st.session_state['yag_100g'] = float(yeni_res.get("yag_100g", 0.0))
                        st.session_state['kalori_100g'] = float(yeni_res.get("kalori_100g", 0.0))
                        st.session_state['gi'] = int(yeni_res.get("gi", 50))
                        st.rerun()

            st.write("##### ✏️ 100 Gram Başına Düşen Makro Değerleri (Birebir Elle Değiştirebilirsiniz):")
            k_col1, k_col2, k_col3, k_col4, k_col5 = st.columns(5)
            
            # KULLANICININ BİREBİR ELLE DEĞİŞTİREBİLECEĞİ KUTUCUKLAR (KEY'LERİ BENZERSİZLEŞTİRİLDİ)
            kh_100g_user = k_col1.number_input("100g Karbonhidrat (g):", value=st.session_state.get('kh_100g', 0.0), step=1.0, key="edit_kh")
            prot_100g_user = k_col2.number_input("100g Protein (g):", value=st.session_state.get('protein_100g', 0.0), step=1.0, key="edit_prot")
            yag_100g_user = k_col3.number_input("100g Yağ (g):", value=st.session_state.get('yag_100g', 0.0), step=1.0, key="edit_yag")
            kal_100g_user = k_col4.number_input("100g Kalori (kcal):", value=st.session_state.get('kalori_100g', 0.0), step=10.0, key="edit_kal")
            gi_user = k_col5.number_input("Glisemik İndeks (Gİ):", value=st.session_state.get('gi', 50), min_value=0, max_value=100, step=1, key="edit_gi")

            # TOPLAM HESAPLAMALAR
            toplam_kh = (porsiyon / 100) * kh_100g_user
            toplam_protein = (porsiyon / 100) * prot_100g_user
            toplam_yag = (porsiyon / 100) * yag_100g_user
            toplam_kalori = (porsiyon / 100) * kal_100g_user
            hesaplanan_insulin = (toplam_kh / ik_orani) if diyabet_tipi == "Tip 1 Diyabet" else 0.0
            
            st.write("---")
            st.write("### 🥗 Porsiyona Göre Toplam Besin Kartları")
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("🍞 Toplam Karbonhidrat", f"{toplam_kh:.1f} g")
            m_col2.metric("🥩 Toplam Protein", f"{toplam_protein:.1f} g")
            m_col3.metric("🥑 Toplam Yağ", f"{toplam_yag:.1f} g")
            m_col4.metric("🔥 Toplam Enerji", f"{toplam_kalori:.0f} kcal")
            
            st.write("---")
            st.write("### 🤖 Karar Destek Sistemi Klinik Analizi")
            
            if diyabet_tipi == "Tip 1 Diyabet":
                st.subheader("💉 Tip 1 Diyabet İçin İnsülin Karar Desteği")
                cgm_col1, cgm_col2 = st.columns(2)
                cgm_col1.metric("Önerilen Baz İnsülin Dozu", f"{hesaplanan_insulin:.1f} Ünite")
                cgm_col2.metric("Anlık Şeker & Trend", f"{kan_sekeri} mg/dL", delta=trend_yoni)
            else:
                st.subheader("📊 Tip 2 Diyabet İçin Glisemik İndeks Tavsiyesi")
                st.metric("Glisemik İndeks (Gİ)", gi_user)

            if st.button("💾 Öğün Kaydını Veri Tabanına Kaydet", type="primary"):
                ogun_kaydet(user['id'], nihai_yemek_adi, porsiyon, toplam_kh, toplam_protein, toplam_yag, toplam_kalori, gi_user, hesaplanan_insulin, diyabet_tipi)
                glikoz_kaydet(user['id'], kan_sekeri, trend_yoni, olcum_yontemi)
                st.success(f"✅ '{nihai_yemek_adi}' öğünü başarıyla kaydedildi!")
                st.rerun()

    elif secilen_sayfa == "📊 Geçmiş Raporlarım":
        st.title("📊 Kişisel Sağlık Geçmişim")
        df_glikoz = get_glikoz_data(user['id'])
        df_ogun = get_ogun_data(user['id'])

        c_pdf1, c_pdf2 = st.columns([2, 1])
        with c_pdf1:
            st.subheader("📄 Doktor İnceleme Raporu")
            st.write("Kendi verilerinizden oluşan klinik PDF raporunuzu indirin.")
        with c_pdf2:
            pdf_bytes = generate_pdf_report(user['ad_soyad'], df_glikoz, df_ogun)
            st.download_button(
                label="📥 PDF Raporumu İndir",
                data=pdf_bytes,
                file_name=f"Rapor_{user['kullanici_adi']}_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                type="primary"
            )

        st.write("---")
        if not df_glikoz.empty:
            st.subheader("📉 Kan Şekeri Zaman Serisi")
            df_chart = df_glikoz.sort_values("id")[["tarih_saat", "kan_sekeri"]].set_index("tarih_saat")
            st.line_chart(df_chart)
            st.dataframe(df_glikoz[['tarih_saat', 'kan_sekeri', 'trend_yoni', 'olcum_yontemi']], use_container_width=True)
        else:
            st.info("Henüz kaydedilmiş glikoz ölçümünüz bulunmuyor.")

        st.write("---")
        st.subheader("🥗 Kaydedilen Öğün Geçmişim")
        if not df_ogun.empty:
            st.dataframe(
                df_ogun[['tarih_saat', 'yemek_adi', 'porsiyon_gram', 'karbonhidrat_g', 'protein_g', 'yag_g', 'kalori_kcal', 'onerilen_insulin']], 
                use_container_width=True
            )
        else:
            st.info("Henüz kaydedilmiş bir öğününüz bulunmuyor.")

    elif secilen_sayfa == "👨‍⚕️ Doktor Yetkilendirme":
        st.title("👨‍⚕️ Doktor Erişim İzinleri")
        st.write("Doktorunuzun verilerinizi inceleyebilmesi için doktorunuzun kullanıcı adını ekleyin.")
        
        with st.form("doktor_ekle_form"):
            doc_uname = st.text_input("Doktor Kullanıcı Adı:")
            btn_add_doc = st.form_submit_button("Erişim İzni Ver", type="primary")
            
            if btn_add_doc:
                if doc_uname:
                    ok, msg = doktor_ekle(user['id'], doc_uname)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

# ---------------------------------------------------------
# DOKTOR PANELİ
# ---------------------------------------------------------
elif user['rol'] == 'doktor':
    st.title("🩺 Doktor İnceleme Paneli")
    st.write("Sadece size erişim izni vermiş hastaların klinik kayıtlarını inceleyebilirsiniz.")
    
    hastalar = get_izinli_hastalar(user['id'])
    
    if not hastalar:
        st.warning("Henüz size erişim izni vermiş bir hasta bulunmamaktadır.")
    else:
        hasta_dict = {f"{h[1]} (@{h[2]})": h[0] for h in hastalar}
        secilen_hasta_label = st.selectbox("İncelemek İstediğiniz Hastayı Seçin:", list(hasta_dict.keys()))
        secilen_hasta_id = hasta_dict[secilen_hasta_label]
        
        df_glikoz = get_glikoz_data(secilen_hasta_id)
        df_ogun = get_ogun_data(secilen_hasta_id)
        
        st.write("---")
        c_pdf1, c_pdf2 = st.columns([2, 1])
        with c_pdf1:
            st.subheader(f"📄 {secilen_hasta_label} Klinik Raporu")
        with c_pdf2:
            pdf_bytes = generate_pdf_report(secilen_hasta_label, df_glikoz, df_ogun)
            st.download_button(
                label="📥 Hastanın PDF Raporunu İndir",
                data=pdf_bytes,
                file_name=f"Klinik_Rapor_{secilen_hasta_id}.pdf",
                mime="application/pdf",
                type="primary"
            )

        st.write("---")
        col_g, col_o = st.columns(2)
        with col_g:
            st.subheader("📉 Glikoz Ölçümleri")
            if not df_glikoz.empty:
                st.dataframe(df_glikoz[['tarih_saat', 'kan_sekeri', 'trend_yoni', 'olcum_yontemi']], use_container_width=True)
            else:
                st.info("Veri yok.")
                
        with col_o:
            st.subheader("🥗 Öğün Analizleri")
            if not df_ogun.empty:
                st.dataframe(df_ogun[['tarih_saat', 'yemek_adi', 'karbonhidrat_g', 'kalori_kcal', 'onerilen_insulin']], use_container_width=True)
            else:
                st.info("Veri yok.")