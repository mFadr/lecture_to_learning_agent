"""Dialogy: nastavení, spuštění analýzy, souhlas s cloudem, rozsah stránek."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from ..agent.orchestrator import LLM_STEPS, STEP_LABELS, STEP_ORDER, RunOptions
from ..config import LLMMode, Preference, ProviderSettings, Settings, data_dir
from ..llm.factory import build_cloud, build_local
from ..llm.secrets import delete_api_key, get_api_key, store_api_key
from ..models import DetailLevel, Project
from ..pdf.extractor import OcrConfig, find_tessdata

CLOUD_WARNING = ("V cloudovém a hybridním režimu se <b>části textu vašich PDF odesílají externímu poskytovateli</b> "
                 "(podle nastavené adresy API). Odesílají se jen extrahované úseky potřebné pro daný krok, ne celé "
                 "soubory. Poskytovatel může data zpracovávat podle svých podmínek; volání API je zpoplatněno. "
                 "Rozsah lze omezit výběrem stránek dokumentu a limitem počtu volání.")


def ask_cloud_consent(parent: QWidget) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Odesílání dat mimo zařízení")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(CLOUD_WARNING + "<br><br>Souhlasíte s odesíláním vybraných částí dokumentů?")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


class ProviderBox(QGroupBox):
    """Editor parametrů jednoho poskytovatele."""

    def __init__(self, title: str, ps: ProviderSettings, local: bool):
        super().__init__(title)
        self.local = local
        f = QFormLayout(self)
        self.base_url = QLineEdit(ps.base_url)
        self.model = QComboBox(editable=True)
        self.model.setCurrentText(ps.model)
        self.temperature = QDoubleSpinBox(minimum=0, maximum=2, singleStep=0.1, value=ps.temperature)
        self.max_tokens = QSpinBox(minimum=256, maximum=64000, singleStep=256, value=ps.max_output_tokens)
        self.max_chars = QSpinBox(minimum=1500, maximum=400000, singleStep=1000, value=ps.max_request_chars)
        self.ctx = QSpinBox(minimum=0, maximum=262144, singleStep=1024, value=ps.context_tokens)
        self.timeout = QDoubleSpinBox(minimum=10, maximum=3600, singleStep=30, value=ps.timeout_s)
        self.json_mode = QCheckBox("Používat nativní JSON režim modelu (pokud ho model podporuje)")
        self.json_mode.setChecked(ps.json_mode)
        f.addRow("Adresa API:", self.base_url)
        f.addRow("Identifikátor modelu:", self.model)
        f.addRow("Teplota:", self.temperature)
        f.addRow("Max. délka výstupu (tokeny):", self.max_tokens)
        f.addRow("Max. velikost textu v požadavku (znaky):", self.max_chars)
        if local:
            f.addRow("Kontextové okno (num_ctx, tokeny):", self.ctx)
        f.addRow("Časový limit odpovědi (s):", self.timeout)
        f.addRow("", self.json_mode)
        row = QHBoxLayout()
        self.check_btn = QPushButton("Ověřit dostupnost a načíst modely")
        self.status = QLabel(wordWrap=True)
        row.addWidget(self.check_btn)
        f.addRow(row)
        f.addRow(self.status)

    def to_settings(self) -> ProviderSettings:
        return ProviderSettings(model=self.model.currentText().strip(), base_url=self.base_url.text().strip(),
                                temperature=self.temperature.value(), max_output_tokens=self.max_tokens.value(),
                                timeout_s=self.timeout.value(), max_request_chars=self.max_chars.value(),
                                context_tokens=self.ctx.value(), json_mode=self.json_mode.isChecked())

    def show_status(self, ok: bool, msg: str, models: list[str]) -> None:
        self.status.setText(("✔ " if ok else "✖ ") + msg)
        self.status.setStyleSheet(f"color:{'#15803d' if ok else '#b91c1c'}")
        if models:
            cur = self.model.currentText()
            self.model.clear()
            self.model.addItems(models)
            self.model.setCurrentText(cur)


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, settings: Settings):
        super().__init__(parent)
        self.setWindowTitle("Nastavení")
        self.resize(760, 700)
        self.settings = settings.model_copy(deep=True)
        s = self.settings
        tabs = QTabWidget()

        # --- AI model
        ai = QWidget()
        al = QVBoxLayout(ai)
        mode_box = QGroupBox("Režim provozu")
        ml = QVBoxLayout(mode_box)
        self.mode_group = QButtonGroup(self)
        for mode, text in ((LLMMode.LOCAL, "Plně lokální (Ollama) – nic neopouští počítač"),
                           (LLMMode.CLOUD, "Cloudové LLM přes API kompatibilní s OpenAI"),
                           (LLMMode.HYBRID, "Hybridní – lokální zpracování, cloud jen pro vybrané kroky")):
            rb = QRadioButton(text)
            rb.setProperty("mode", mode.value)
            rb.setChecked(s.mode == mode)
            self.mode_group.addButton(rb)
            ml.addWidget(rb)
        pref_row = QHBoxLayout()
        self.pref = QComboBox()
        for p, t in ((Preference.QUALITY, "Kvalita"), (Preference.SPEED, "Rychlost"), (Preference.COST, "Nízké náklady")):
            self.pref.addItem(t, p.value)
        self.pref.setCurrentIndex(self.pref.findData(s.preference.value))
        apply_pref = QPushButton("Použít předvolbu parametrů")
        apply_pref.clicked.connect(self._apply_preference)
        pref_row.addWidget(QLabel("Preference:"))
        pref_row.addWidget(self.pref)
        pref_row.addWidget(apply_pref)
        pref_row.addStretch()
        ml.addLayout(pref_row)
        al.addWidget(mode_box)
        self.local_box = ProviderBox("Lokální model (Ollama)", s.ollama, local=True)
        self.local_box.check_btn.clicked.connect(lambda: self._check(True))
        hint = QLabel("Orientačně: modely se 7–8 mld. parametrů potřebují cca 8 GB volné RAM (bez GPU jsou pomalé); "
                      "menší modely (1–4 mld.) jsou rychlejší, ale méně přesné. Skutečnou rychlost ověřte na svém "
                      "počítači. Pokud model nestíhá, zmenšete „Max. velikost textu v požadavku“.", wordWrap=True)
        hint.setStyleSheet("color:#52606d")
        al.addWidget(self.local_box)
        al.addWidget(hint)
        al.addStretch()
        tabs.addTab(ai, "AI model")

        # --- Cloud
        cl = QWidget()
        cll = QVBoxLayout(cl)
        warn = QLabel(CLOUD_WARNING, wordWrap=True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setStyleSheet("background:#fef3c7;padding:8px")
        cll.addWidget(warn)
        self.consent = QCheckBox("Souhlasím s odesíláním vybraných částí dokumentů cloudovému poskytovateli")
        self.consent.setChecked(s.cloud_consent)
        cll.addWidget(self.consent)
        name_row = QFormLayout()
        self.provider_name = QLineEdit(s.cloud_provider_name)
        name_row.addRow("Název poskytovatele:", self.provider_name)
        cll.addLayout(name_row)
        self.cloud_box = ProviderBox("Cloudový model", s.cloud, local=False)
        self.cloud_box.check_btn.clicked.connect(lambda: self._check(False))
        cll.addWidget(self.cloud_box)
        key_box = QGroupBox("API klíč")
        kl = QFormLayout(key_box)
        self.key_status = QLabel()
        self.key_edit = QLineEdit(echoMode=QLineEdit.EchoMode.Password, placeholderText="vložte klíč a uložte")
        kb = QHBoxLayout()
        save_key, del_key = QPushButton("Uložit do Správce pověření Windows"), QPushButton("Smazat uložený klíč")
        save_key.clicked.connect(self._save_key)
        del_key.clicked.connect(self._delete_key)
        kb.addWidget(save_key)
        kb.addWidget(del_key)
        kl.addRow("Stav:", self.key_status)
        kl.addRow("Nový klíč:", self.key_edit)
        kl.addRow(kb)
        kl.addRow(QLabel("Klíč lze také zadat proměnnou prostředí L2L_API_KEY nebo OPENAI_API_KEY. Klíč se nikdy "
                         "neukládá do nastavení ani do logů.", wordWrap=True))
        cll.addWidget(key_box)
        lim = QFormLayout()
        self.max_calls = QSpinBox(minimum=0, maximum=10000, value=s.cloud_max_calls_per_run)
        lim.addRow("Max. počet volání API na jeden běh (0 = bez limitu):", self.max_calls)
        cll.addLayout(lim)
        hyb = QGroupBox("Hybridní režim – kroky zpracované v cloudu")
        hl = QHBoxLayout(hyb)
        self.hybrid_checks: dict[str, QCheckBox] = {}
        for step in STEP_ORDER:
            if step in LLM_STEPS:
                cb = QCheckBox(STEP_LABELS[step])
                cb.setChecked(step in s.hybrid_cloud_steps)
                self.hybrid_checks[step] = cb
                hl.addWidget(cb)
        cll.addWidget(hyb)
        cll.addStretch()
        tabs.addTab(cl, "Cloudové API")
        self._refresh_key_status()

        # --- OCR
        ocr = QWidget()
        ol = QFormLayout(ocr)
        self.ocr_enabled = QCheckBox("Použít OCR pro stránky bez textové vrstvy (skeny)")
        self.ocr_enabled.setChecked(s.ocr_enabled)
        self.ocr_lang = QLineEdit(s.ocr_language)
        self.tess_path = QLineEdit(s.tesseract_path)
        browse = QPushButton("Vybrat složku Tesseractu…")
        browse.clicked.connect(lambda: self.tess_path.setText(
            QFileDialog.getExistingDirectory(self, "Složka Tesseract-OCR") or self.tess_path.text()))
        self.ocr_status = QLabel(wordWrap=True)
        test = QPushButton("Zkontrolovat Tesseract")
        test.clicked.connect(self._check_ocr)
        ol.addRow(self.ocr_enabled)
        ol.addRow("Jazyky OCR:", self.ocr_lang)
        ol.addRow("Cesta k Tesseractu:", self.tess_path)
        ol.addRow(browse)
        ol.addRow(test)
        ol.addRow(self.ocr_status)
        ol.addRow(QLabel("Tesseract je volitelná komponenta: https://github.com/UB-Mannheim/tesseract/wiki – při instalaci "
                         "zvolte češtinu (ces).", wordWrap=True))
        tabs.addTab(ocr, "OCR")
        self._check_ocr()

        # --- Data a export
        dt = QWidget()
        dl = QFormLayout(dt)
        self.data_dir = QLineEdit(s.data_dir)
        self.data_dir.setPlaceholderText(str(data_dir(Settings())))
        open_dir = QPushButton("Otevřít datovou složku")
        open_dir.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir(self.settings)))))
        self.copy_sources = QCheckBox("Ukládat kopii PDF do projektu (jinak jen odkaz na originál)")
        self.copy_sources.setChecked(s.copy_sources_default)
        dl.addRow("Datová složka (změna po restartu):", self.data_dir)
        dl.addRow(open_dir)
        dl.addRow(self.copy_sources)
        self.export_checks: dict[str, QCheckBox] = {}
        exp = QGroupBox("Výchozí formáty exportu")
        el = QHBoxLayout(exp)
        for k, t in (("md", "Markdown"), ("html", "HTML"), ("docx", "DOCX"), ("csv", "CSV"), ("tsv", "Anki TSV"),
                     ("json", "JSON"), ("png", "PNG"), ("svg", "SVG")):
            cb = QCheckBox(t)
            cb.setChecked(k in s.export_formats)
            self.export_checks[k] = cb
            el.addWidget(cb)
        dl.addRow(exp)
        tabs.addTab(dt, "Data a export")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _apply_preference(self) -> None:
        p = Preference(self.pref.currentData())
        lb, cb = self.local_box, self.cloud_box
        if p == Preference.SPEED:
            lb.max_chars.setValue(5000); lb.max_tokens.setValue(1536); lb.ctx.setValue(4096)  # noqa: E702
        elif p == Preference.QUALITY:
            lb.max_chars.setValue(9000); lb.max_tokens.setValue(3072); lb.ctx.setValue(8192)  # noqa: E702
            cb.max_chars.setValue(30000); cb.max_tokens.setValue(4096)  # noqa: E702
        else:
            cb.max_chars.setValue(16000); cb.max_tokens.setValue(2048); self.max_calls.setValue(30)  # noqa: E702

    def _collect(self) -> Settings:
        s = self.settings
        s.mode = LLMMode(self.mode_group.checkedButton().property("mode"))
        s.preference = Preference(self.pref.currentData())
        s.ollama = self.local_box.to_settings()
        s.cloud = self.cloud_box.to_settings()
        s.cloud_provider_name = self.provider_name.text().strip() or "Cloud API"
        s.cloud_consent = self.consent.isChecked()
        s.cloud_max_calls_per_run = self.max_calls.value()
        s.hybrid_cloud_steps = [k for k, cb in self.hybrid_checks.items() if cb.isChecked()]
        s.ocr_enabled = self.ocr_enabled.isChecked()
        s.ocr_language = self.ocr_lang.text().strip() or "ces+eng"
        s.tesseract_path = self.tess_path.text().strip()
        s.data_dir = self.data_dir.text().strip()
        s.copy_sources_default = self.copy_sources.isChecked()
        s.export_formats = [k for k, cb in self.export_checks.items() if cb.isChecked()]
        return s

    def _check(self, local: bool) -> None:
        s = self._collect()
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            p = build_local(s) if local else build_cloud(s)
            st = p.check()
        finally:
            QGuiApplication.restoreOverrideCursor()
        (self.local_box if local else self.cloud_box).show_status(st.ok, st.message, st.models)

    def _check_ocr(self) -> None:
        td = find_tessdata(OcrConfig(True, self.ocr_lang.text(), self.tess_path.text()))
        self.ocr_status.setText(f"✔ Tesseract nalezen ({td})" if td else "✖ Tesseract nebyl nalezen – OCR nebude fungovat.")

    def _refresh_key_status(self) -> None:
        key, source = get_api_key()
        self.key_status.setText(f"nastaven ({source})" if key else "nenastaven")

    def _save_key(self) -> None:
        if not self.key_edit.text().strip():
            return
        try:
            store_api_key(self.key_edit.text())
            self.key_edit.clear()
        except Exception as exc:
            QMessageBox.warning(self, "API klíč", f"Klíč se nepodařilo uložit: {exc}")
        self._refresh_key_status()

    def _delete_key(self) -> None:
        delete_api_key()
        self._refresh_key_status()

    def _accept(self) -> None:
        s = self._collect()
        if s.mode != LLMMode.LOCAL and not s.cloud_consent:
            if ask_cloud_consent(self):
                s.cloud_consent = True
                self.consent.setChecked(True)
            else:
                QMessageBox.information(self, "Režim", "Bez souhlasu s přenosem dat zůstane aktivní lokální režim.")
                s.mode = LLMMode.LOCAL
        self.settings = s
        self.accept()


class AnalysisDialog(QDialog):
    """Výběr kroků workflow a parametrů běhu."""

    def __init__(self, parent: QWidget, project: Project, settings: Settings, mode_desc: str):
        super().__init__(parent)
        self.setWindowTitle("Spustit zpracování")
        self.resize(560, 620)
        lay = QVBoxLayout(self)
        info = QLabel(f"<b>Model:</b> {mode_desc}", wordWrap=True)
        if settings.mode != LLMMode.LOCAL:
            info.setStyleSheet("background:#fef3c7;padding:6px")
            info.setText(info.text() + "<br>Části textu budou odeslány cloudovému poskytovateli.")
        lay.addWidget(info)
        steps_box = QGroupBox("Kroky / výstupy")
        sl = QVBoxLayout(steps_box)
        self.step_checks: dict[str, QCheckBox] = {}
        for step in STEP_ORDER:
            cb = QCheckBox(STEP_LABELS[step])
            cb.setChecked(True)
            self.step_checks[step] = cb
            sl.addWidget(cb)
        lay.addWidget(steps_box)
        form = QFormLayout()
        self.detail = QComboBox()
        for d in DetailLevel:
            self.detail.addItem(d.label, d.value)
        self.detail.setCurrentIndex(self.detail.findData(settings.detail_level))
        self.n_cards = QSpinBox(minimum=5, maximum=300, value=30)
        self.n_q = QSpinBox(minimum=3, maximum=200, value=15)
        self.n_viz = QSpinBox(minimum=0, maximum=30, value=4)
        self.force = QCheckBox("Ignorovat uložené mezivýsledky (znovu volat model)")
        form.addRow("Úroveň detailu shrnutí:", self.detail)
        form.addRow("Počet kartiček (přibližně):", self.n_cards)
        form.addRow("Počet testových otázek (přibližně):", self.n_q)
        form.addRow("Automaticky vizualizovat příkladů:", self.n_viz)
        form.addRow(self.force)
        lay.addLayout(form)
        lay.addWidget(QLabel("Dokumenty (rozsah stránek nastavíte v seznamu dokumentů):"))
        self.docs = QListWidget()
        for d in project.documents:
            it = QListWidgetItem(f"{d.filename} ({d.page_count} s." +
                                 (f", strany {d.page_from or 1}–{d.page_to or d.page_count}" if d.page_from or d.page_to else "")
                                 + ")")
            it.setData(Qt.ItemDataRole.UserRole, d.id)
            it.setCheckState(Qt.CheckState.Checked)
            self.docs.addItem(it)
        lay.addWidget(self.docs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Spustit")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def options(self) -> RunOptions:
        doc_ids = [self.docs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.docs.count())
                   if self.docs.item(i).checkState() == Qt.CheckState.Checked]
        return RunOptions(steps=[s for s, cb in self.step_checks.items() if cb.isChecked()],
                          detail=DetailLevel(self.detail.currentData()), doc_ids=doc_ids,
                          n_flashcards=self.n_cards.value(), n_questions=self.n_q.value(),
                          max_auto_viz=self.n_viz.value(), force=self.force.isChecked())


class PageRangeDialog(QDialog):
    def __init__(self, parent: QWidget, page_count: int, start: int | None, end: int | None):
        super().__init__(parent)
        self.setWindowTitle("Rozsah stránek")
        f = QFormLayout(self)
        self.start = QSpinBox(minimum=1, maximum=page_count, value=start or 1)
        self.end = QSpinBox(minimum=1, maximum=page_count, value=end or page_count)
        f.addRow("Od strany:", self.start)
        f.addRow("Do strany:", self.end)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def values(self) -> tuple[int, int]:
        a, b = self.start.value(), self.end.value()
        return min(a, b), max(a, b)


def open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 – otevření složky v Průzkumníku na žádost uživatele
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
