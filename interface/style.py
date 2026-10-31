"""Apparence : sobre, lisible en 1366x768, sans animation."""

COULEUR_PRINCIPALE = "#e8590c"

FEUILLE_DE_STYLE = f"""
QWidget {{ font-size: 11pt; }}
QMainWindow, QDialog {{ background: #f5f6f8; }}
QListWidget#menu {{ background: #1f2937; color: #e5e7eb; border: none; padding-top: 8px; outline: 0; }}
QListWidget#menu::item {{ padding: 12px 16px; }}
QListWidget#menu::item:selected {{ background: {COULEUR_PRINCIPALE}; color: white; }}
QListWidget#menu::item:disabled {{ color: #6b7280; }}
QWidget#colonne_menu {{ background: #1f2937; }}
QLabel#note_menu {{ color: #9ca3af; padding: 10px 16px; font-size: 9pt; }}
QLabel#titre {{ font-size: 18pt; font-weight: bold; color: #111827; }}
QLabel#info {{ color: #4b5563; }}
QLineEdit, QComboBox, QSpinBox {{ padding: 6px; border: 1px solid #cbd5e1; border-radius: 4px; background: white; }}
QPushButton {{ padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 4px; background: white; }}
QPushButton:hover {{ background: #eef2f7; }}
QPushButton#principal {{ background: {COULEUR_PRINCIPALE}; color: white; border: none; font-weight: bold; }}
QPushButton#principal:hover {{ background: #c2410c; }}
QPushButton:disabled {{ color: #9ca3af; background: #f3f4f6; }}
QTableWidget {{ background: white; alternate-background-color: #f9fafb; gridline-color: #e5e7eb;
               selection-background-color: #fed7aa; selection-color: #111827; }}
QHeaderView::section {{ background: #e5e7eb; padding: 6px; border: none; font-weight: bold; }}
"""
