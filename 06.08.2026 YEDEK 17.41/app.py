import os
import io
import json
import re
import pandas as pd
import sqlite3
import streamlit as st
from PIL import Image
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ReportLab PDF Kütüphaneleri
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# 1. SAYFA YAPILANDIRMASI VE API ANAHTARI
# ---------------------------------------------------------
st.set_page_config(
    page_title="Diyabetik Beslenme Karar Destek", 
    page_icon="🥗", 
    layout="wide"
)

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    st.error("⚠️ API Key bulunamadı! Lütfen .env dosyanızı kontrol edin.")
    st.stop()

client = genai.Client(api_key=API_KEY)

# Session State Başlatma
if 'analiz_yapildi' not in st.session_state:
    st.session_state['analiz_yapildi'] = False

# ---------------------------------------------------------
# 2. SQLITE VERİ TABANI YÖNETİM KATMANI
# ---------------------------------------------------------
DB_FILE = "diyabet_takip.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS glikoz_kayitlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih_saat TEXT NOT NULL,
            kan_sekeri INTEGER NOT NULL,
            trend_yoni TEXT,
            olcum_yontemi TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ogun_kayitlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih_saat TEXT NOT NULL,
            yemek_adi TEXT NOT NULL,
            porsiyon_gram INTEGER,
            karbonhidrat_g REAL,
            protein_g REAL,
            yag_g REAL,
            kalori_kcal REAL,
            glisemik_indeks INTEGER,
            onerilen_insulin REAL,
            diyabet_tipi TEXT
        )
    ''')
    conn.commit()
    conn.close()

def glikoz_kaydet(kan_sekeri, trend_yoni, olcum_yontemi):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO glikoz_kayitlari (tarih_saat, kan_sekeri, trend_yoni, olcum_yontemi)
        VALUES (?, ?, ?, ?)
    ''', (simdi, kan_sekeri, trend_yoni, olcum_yontemi))
    conn.commit()
    conn.close()

def ogun_kaydet(yemek_adi, porsiyon, kh, prot, yag, kal, gi, insulin, diyabet_tipi):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO ogun_kayitlari 
        (tarih_saat, yemek_adi, porsiyon_gram, karbonhidrat_g, protein_g, yag_g, kalori_kcal, glisemik_indeks, onerilen_insulin, diyabet_tipi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (simdi, yemek_adi, porsiyon, kh, prot, yag, kal, gi, insulin, diyabet_tipi))
    conn.commit()
    conn.close()

def get_glikoz_data():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM glikoz_kayitlari ORDER BY id DESC", conn)
    conn.close()
    return df

def get_ogun_data():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM ogun_kayitlari ORDER BY id DESC", conn)
    conn.close()
    return df

init_db()

# ---------------------------------------------------------
# 3. DOKTOR PDF RAPORU ÜRETME KATMANI
# ---------------------------------------------------------
def generate_pdf_report(df_glikoz, df_ogun):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#1E3A8A'),
        alignment=1
    )
    subtitle_style = ParagraphStyle(
        'SubtitleStyle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#4B5563'),
        alignment=1
    )
    h2_style = ParagraphStyle(
        'H2Style',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#1F2937'),
        spaceBefore=12,
        spaceAfter=6
    )

    story.append(Paragraph("DIYABETIK BESLENME VE KAN SEKERI KLINIK RAPORU", title_style))
    story.append(Paragraph(f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Diyabetik Karar Destek Sistemi", subtitle_style))
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
# 4. NAVİGASYON (SAYFA SEÇİMİ)
# ---------------------------------------------------------
st.sidebar.title("📌 Navigasyon")
secilen_sayfa = st.sidebar.radio(
    "Gitmek İstediğiniz Modül:",
    ["🥗 Beslenme & Karar Destek", "📊 Geçmiş Raporlar & İstatistikler"]
)

# ---------------------------------------------------------
# SAYFA 1: BESLENME & KARAR DESTEK
# ---------------------------------------------------------
if secilen_sayfa == "🥗 Beslenme & Karar Destek":
    st.title("🥗 Yapay Zeka Destekli Diyabetik Beslenme Karar Destek Sistemi")
    
    st.sidebar.write("---")
    st.sidebar.header("👤 Hasta Profili Ayarları")
    diyabet_tipi = st.sidebar.radio("Diyabet Tipinizi Seçin:", ["Tip 1 Diyabet", "Tip 2 Diyabet"])

    st.sidebar.write("---")
    st.sidebar.header("📡 Kan Şekeri Ölçüm Modu")
    olcum_yontemi = st.sidebar.radio(
        "Girdi Yöntemi:",
        ["Manuel Ölçüm (Parmaktan)", "📡 Canlı Sensör / Telefon Entegrasyonu"]
    )

    trend_yoni = "➡️ Stabil"

    if olcum_yontemi == "📡 Canlı Sensör / Telefon Entegrasyonu":
        query_params = st.query_params
        if "seker" in query_params:
            kan_sekeri = int(query_params["seker"])
            trend_kod = query_params.get("trend", "stabil")
            trend_yoni = "⇈ Hızla Yükseliyor" if trend_kod == "yukseliyor" else ("⇊ Hızla Düşüyor" if trend_kod == "dusuyor" else "➡️ Stabil")
            st.sidebar.success("📱 Canlı Veri Bağlantısı Alındı")
        else:
            st.sidebar.warning("⚠️ Simülasyon Modu Aktif")
            kan_sekeri = st.sidebar.number_input("Anlık Sensör Verisi (mg/dL):", min_value=50, max_value=400, value=145)

        st.sidebar.info(f"📊 Sensör Trendi: **{trend_yoni}**")
    else:
        kan_sekeri = st.sidebar.number_input("Mevcut Kan Şekeri (mg/dL):", min_value=50, max_value=400, value=120)

    if st.sidebar.button("💾 Kan Şekerini Kaydet"):
        glikoz_kaydet(kan_sekeri, trend_yoni, olcum_yontemi)
        st.sidebar.success(f"✅ {kan_sekeri} mg/dL kaydedildi!")

    ik_orani = st.sidebar.number_input("İnsülin / Karbonhidrat Oranı (1 Ünite / X g KH):", min_value=1, max_value=50, value=15) if diyabet_tipi == "Tip 1 Diyabet" else 15

    st.write("---")
    yuklenen_dosya = st.file_uploader("Bir yemek fotoğrafı yükleyin...", type=["jpg", "jpeg", "png", "webp"])

    if yuklenen_dosya is not None:
        resim = Image.open(yuklenen_dosya)
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.image(resim, caption="Yüklenen Tabağın Görseli", use_container_width=True)
            
        with col2:
            st.subheader("🔍 Yapay Zeka Analizi")
            if st.button("🚀 Tabağı Analiz Et", type="primary", use_container_width=True):
                with st.spinner("Yapay zeka görseli inceliyor..."):
                    try:
                        # OtoAI'deki Dinamik Model Arama Mantığı
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
                            # OtoAI'deki Esnek Regex JSON Ayıklama
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
                                st.error("❌ Görsel çözümlenemedi veya besin verisi bulunamadı.")
                        else:
                            st.error("❌ Model yanıt vermedi. Lütfen internet bağlantınızı veya API anahtarınızı kontrol edin.")
                    except Exception as e:
                        st.error(f"Sistem Hatası: {e}")

        if st.session_state.get('analiz_yapildi', False):
            st.write("---")
            st.subheader("🎯 Görsel Tanılama ve Besin Verisi Onayı")
            
            c_1, c_2 = st.columns([2, 1])
            nihai_yemek_adi = c_1.text_input("Tespit Edilen Yemek Adı:", value=st.session_state['tespit_edilen'])
            porsiyon = c_2.number_input("Porsiyon Gramajı (g):", min_value=10, max_value=2000, value=st.session_state['porsiyon'])

            toplam_kh = (porsiyon / 100) * st.session_state['kh_100g']
            toplam_protein = (porsiyon / 100) * st.session_state['protein_100g']
            toplam_yag = (porsiyon / 100) * st.session_state['yag_100g']
            toplam_kalori = (porsiyon / 100) * st.session_state['kalori_100g']
            gi_degeri = st.session_state['gi']
            hesaplanan_insulin = (toplam_kh / ik_orani) if diyabet_tipi == "Tip 1 Diyabet" else 0.0
            
            st.write("### 🥗 Öğün Makro Besin Kartları")
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("🍞 Karbonhidrat", f"{toplam_kh:.1f} g")
            m_col2.metric("🥩 Protein", f"{toplam_protein:.1f} g")
            m_col3.metric("🥑 Yağ", f"{toplam_yag:.1f} g")
            m_col4.metric("🔥 Enerji", f"{toplam_kalori:.0f} kcal")
            
            st.write("---")
            st.write("### 🤖 Karar Destek Sistemi Klinik Analizi")
            
            if diyabet_tipi == "Tip 1 Diyabet":
                st.subheader("💉 Tip 1 Diyabet İçin İnsülin Karar Desteği")
                cgm_col1, cgm_col2 = st.columns(2)
                cgm_col1.metric("Önerilen Baz İnsülin Dozu", f"{hesaplanan_insulin:.1f} Ünite")
                cgm_col2.metric("Anlık Şeker & Trend", f"{kan_sekeri} mg/dL", delta=trend_yoni)
            else:
                st.subheader("📊 Tip 2 Diyabet İçin Glisemik İndeks Tavsiyesi")
                st.metric("Glisemik İndeks (Gİ)", gi_degeri)

            if st.button("💾 Öğün Kaydını Veri Tabanına Kaydet", type="primary"):
                ogun_kaydet(nihai_yemek_adi, porsiyon, toplam_kh, toplam_protein, toplam_yag, toplam_kalori, gi_degeri, hesaplanan_insulin, diyabet_tipi)
                glikoz_kaydet(kan_sekeri, trend_yoni, olcum_yontemi)
                st.success(f"✅ '{nihai_yemek_adi}' öğünü veri tabanına kalıcı olarak kaydedildi!")

# ---------------------------------------------------------
# SAYFA 2: GEÇMİŞ RAPORLAR VE DOKTOR PDF İNDİRME
# ---------------------------------------------------------
else:
    st.title("📊 Geçmiş Raporlar ve Doktor İnceleme Modülü")
    st.write("Bu sekmede SQLite veri tabanına kaydedilmiş verilerinizi inceleyebilir ve doktorunuz için PDF formatında rapor indirebilirsiniz.")
    
    df_glikoz = get_glikoz_data()
    df_ogun = get_ogun_data()

    st.write("---")
    c_pdf1, c_pdf2 = st.columns([2, 1])
    with c_pdf1:
        st.subheader("📄 Doktor İnceleme Raporu Üretici")
        st.write("Tüm glikoz ölçümlerinizi ve öğün verilerinizi içeren klinik PDF raporunu tek tıkla indirin.")
    with c_pdf2:
        pdf_bytes = generate_pdf_report(df_glikoz, df_ogun)
        st.download_button(
            label="📥 Doktor Raporunu İndir (PDF)",
            data=pdf_bytes,
            file_name=f"Diyabet_Klinik_Rapor_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            type="primary"
        )
    
    st.write("---")
    st.subheader("📈 Kan Şekeri Ölçüm Geçmişi ve İstatistikler")
    
    if not df_glikoz.empty:
        k_col1, k_col2, k_col3 = st.columns(3)
        ort_seker = df_glikoz['kan_sekeri'].mean()
        max_seker = df_glikoz['kan_sekeri'].max()
        min_seker = df_glikoz['kan_sekeri'].min()
        
        k_col1.metric("Ortalama Kan Şekeri", f"{ort_seker:.1f} mg/dL")
        k_col2.metric("En Yüksek Ölçüm", f"{max_seker} mg/dL")
        k_col3.metric("En Düşük Ölçüm", f"{min_seker} mg/dL")
        
        st.write("#### 📉 Kan Şekeri Zaman Serisi Grafiği")
        df_chart = df_glikoz.sort_values("id")[["tarih_saat", "kan_sekeri"]].set_index("tarih_saat")
        st.line_chart(df_chart)
        
        with st.expander("📄 Kan Şekeri Ölçüm Veri Tablosu (SQLite)"):
            st.dataframe(df_glikoz, use_container_width=True)
    else:
        st.info("Henüz kaydedilmiş kan şekeri ölçümü bulunamadı.")
        
    st.write("---")
    st.subheader("🥗 Geçmiş Öğün Kayıtları ve Makro Özetler")
    
    if not df_ogun.empty:
        o_col1, o_col2, o_col3, o_col4 = st.columns(4)
        o_col1.metric("Toplam Tüketilen KH", f"{df_ogun['karbonhidrat_g'].sum():.1f} g")
        o_col2.metric("Toplam Tüketilen Protein", f"{df_ogun['protein_g'].sum():.1f} g")
        o_col3.metric("Toplam Tüketilen Yağ", f"{df_ogun['yag_g'].sum():.1f} g")
        o_col4.metric("Toplam Alınan Kalori", f"{df_ogun['kalori_kcal'].sum():.0f} kcal")
        
        st.write("#### 📋 Öğün Analiz Tablosu")
        st.dataframe(df_ogun[['tarih_saat', 'yemek_adi', 'porsiyon_gram', 'karbonhidrat_g', 'kalori_kcal', 'onerilen_insulin', 'diyabet_tipi']], use_container_width=True)
    else:
        st.info("Henüz kaydedilmiş öğün analizi bulunamadı.")