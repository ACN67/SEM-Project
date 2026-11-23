from argparse import ArgumentParser
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from PIL import Image, ImageDraw, ImageFont

parser = ArgumentParser(description="Generate the K1 Chinese export probe")
parser.add_argument("output_dir", type=Path)
parser.add_argument("font_file", type=Path, help="Microsoft YaHei TTF/TTC file")
args = parser.parse_args()
root = args.output_dir
root.mkdir(parents=True, exist_ok=True)
series = [("2022", 688.7), ("2023", 949.5), ("2024", 1286.6)]
image = Image.new("RGB", (680, 330), "white")
draw = ImageDraw.Draw(image)
font = ImageFont.truetype(str(args.font_file), 23)
draw.text((20, 12), "年度销量（万辆）", font=font, fill="#17365d")
for index, (year, value) in enumerate(series):
    y = 70 + index * 80
    end = 105 + int(value / 3.4)
    draw.text((20, y), year, font=font, fill="#17365d")
    draw.rectangle((105, y, end, y + 30), fill="#4472c4")
    draw.text((end + 12, y), str(value), font=font, fill="#17365d")
image.save(root / "sales.png")

document = Document()
for name in ("Normal", "Title", "Heading 1"):
    style = document.styles[name]
    style.font.name = "Microsoft YaHei"
    style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
document.styles["Normal"].font.size = Pt(11)
document.add_heading("新能源汽车销量｜中文导出试样", 0)
document.add_heading("市场规模", 1)
document.add_paragraph("2024 年销量为 1286.6 万辆。数据来源见参考文献 [1]。")
table = document.add_table(rows=1, cols=2)
table.style = "Table Grid"
for cell, value in zip(table.rows[0].cells, ("年度", "销量（万辆）")):
    cell.text = value
for year, value in series:
    cells = table.add_row().cells
    cells[0].text, cells[1].text = year, str(value)
document.add_picture(str(root / "sales.png"), width=Inches(5.3))
document.add_paragraph("图 1　新能源汽车年度销量（来源 [1]）")
document.add_heading("参考文献", 1)
document.add_paragraph("[1] 生态环境部《中国移动源环境管理年报（2025年）》第 I 部分，原件第 8 页图 2。")
document.save(root / "k1-probe.docx")
