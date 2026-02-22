from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from .models import PurchaseOrder


styles = getSampleStyleSheet()


def purchase_order_to_excel(po: PurchaseOrder) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Order"

    ws["A1"] = f"Purchase Order #{po.id}"
    ws["A1"].font = Font(size=16, bold=True)
    ws["A3"] = "Vendor"
    ws["B3"] = po.vendor.name
    ws["A4"] = "Order Date"
    ws["B4"] = po.order_date.isoformat()
    ws["A5"] = "GSTIN"
    ws["B5"] = po.vendor.gst_number
    ws["A6"] = "Address"
    ws["B6"] = po.vendor.full_address
    ws["B6"].alignment = Alignment(wrap_text=True)

    ws.append([])
    ws.append(["S.No", "Material", "Code", "Quantity", "Unit"])
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = Font(bold=True)

    for index, item in enumerate(po.items.select_related("material").all(), start=1):
        ws.append([index, item.material.name, item.material.code, float(item.quantity), item.unit])

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 10

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def purchase_order_to_pdf(po: PurchaseOrder) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=10 * mm)
    elements = []

    elements.append(Paragraph(f"Purchase Order #{po.id}", styles["Title"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Vendor: {po.vendor.name}", styles["Normal"]))
    elements.append(Paragraph(f"Date: {po.order_date.isoformat()}", styles["Normal"]))
    elements.append(Paragraph(f"GSTIN: {po.vendor.gst_number}", styles["Normal"]))
    elements.append(Paragraph(f"Address: {po.vendor.full_address}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    data = [["S.No", "Material", "Code", "Quantity", "Unit"]]
    for index, item in enumerate(po.items.select_related("material").all(), start=1):
        data.append([str(index), item.material.name, item.material.code, str(item.quantity), item.unit])

    table = Table(data, colWidths=[18 * mm, 68 * mm, 34 * mm, 30 * mm, 20 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
            ]
        )
    )
    elements.append(table)

    doc.build(elements)
    return buffer.getvalue()
