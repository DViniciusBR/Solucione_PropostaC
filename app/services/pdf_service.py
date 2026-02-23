from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

def gerar_pdf(dados: dict):

    file_name = f"proposta_{dados['id']}.pdf"
    doc = SimpleDocTemplate(file_name)

    elements = []

    styles = getSampleStyleSheet()
    normal_style = styles["Normal"]

    elements.append(Paragraph("Proposta Comercial", styles["Heading1"]))
    elements.append(Spacer(1, 0.5 * inch))

    elements.append(Paragraph(f"Cliente: {dados['cliente']}", normal_style))
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph(f"Valor: R$ {dados['valor']}", normal_style))

    doc.build(elements)

    return file_name