# Sketch2CAD

כלי מקומי שהופך סקיצות, צילומים וקבצי PDF לשרטוטי AutoCAD (DXF, DWG, PDF). הכלי כולל ניהול פרויקטים, קוד פנימי למסמכים, תבניות כותרת וחיפוש, בעברית ובאנגלית.

- **אפיון:** [docs/SPEC.md](docs/SPEC.md). סטטוס: טיוטה לאישור.
- **אב-טיפוס:** `prototypes/`. אלה הסקריפטים שבנו את שרטוטי הגמל (RFI-0021) והצעת תעלות הניקוז של מבנה P. הקבצים שהם הפיקו נמצאים ב-`prototypes/outputs/`.

## הרצת אב-הטיפוס
```bash
pip install ezdxf matplotlib python-bidi
python prototypes/camel_asmade_simple.py --png
```

הסקריפט מפיק קובץ DXF. כדי להמיר אותו ל-DWG צריך את ODA File Converter (חינמי לשימוש פנימי).
