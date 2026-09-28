"""Editable Word renderer for the same blocks used by the HTML renderer."""
import io
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

FONT='Noto Sans CJK KR'


def anchor(value):return 'ref_'+re.sub(r'[^A-Za-z0-9_]','_',value)[:35]


def render(view):
    doc=Document();section=doc.sections[0]
    section.page_width=Mm(210);section.page_height=Mm(297)
    section.top_margin=section.bottom_margin=Mm(19)
    section.left_margin=section.right_margin=Mm(20)
    section.header_distance=section.footer_distance=Mm(9)
    core=doc.core_properties
    core.author='';core.last_modified_by='';core.comments='';core.keywords=''
    core.title=view['title'][:255];core.subject=view['reader_name']
    try:core.created=core.modified=datetime.fromisoformat(view['generated_at']).astimezone(timezone.utc)
    except ValueError:
        # A masked timestamp must not break distribution or retain template dates.
        for tag in ('dcterms:created','dcterms:modified'):
            node=core._element.find(qn(tag))
            if node is not None:core._element.remove(node)
    # Bundled/default templates may carry colored Title borders. The agreed
    # public-report style is plain black, with borders only in tables.
    for border in list(doc.styles.element.iter(qn('w:pBdr'))):border.getparent().remove(border)
    for name,size in [('Normal',10.5),('Title',18),('Heading 1',13),('Heading 2',11),('Caption',9)]:
        style=doc.styles[name];style.font.name=FONT;style.font.size=Pt(size);style.font.color.rgb=RGBColor(0,0,0)
        style.font.bold=name in ('Title','Heading 1','Heading 2')
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),FONT)
        style.paragraph_format.space_after=Pt(5)
        style.paragraph_format.line_spacing=1.2
        style.paragraph_format.widow_control=True
        if name.startswith('Heading'):
            style.paragraph_format.keep_with_next=True
            style.paragraph_format.space_before=Pt(12)
        # Word's default Heading styles carry theme colors; remove theme attrs.
        color=style.element.get_or_add_rPr().find(qn('w:color'))
        if color is not None:
            for attr in ('themeColor','themeShade','themeTint'):color.attrib.pop(qn('w:'+attr),None)
    title=doc.add_paragraph(view['title'],'Title');title.alignment=WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(view['reader_name']+' · '+view['status'])
    meta=doc.add_paragraph('기준시각 '+view['generated_at']+'\n보고서 '+view['report_id'],'Caption')
    meta.paragraph_format.space_after=Pt(8)
    header=section.header.paragraphs[0];header.text='Evidence Frontier · '+view['reader_name']
    header.style=doc.styles['Caption'];header.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    footer=section.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    footer.style=doc.styles['Caption'];footer.add_run('기준시각 현재 · ')
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
    bookmark=0
    for block in view['blocks']:
        kind=block['kind']
        if kind=='heading':doc.add_heading(block['text'],level=block['level'])
        elif kind=='paragraph':doc.add_paragraph(block['text'])
        elif kind=='page_break':doc.add_page_break()
        elif kind=='anchor':
            bookmark+=1
            start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(bookmark));start.set(qn('w:name'),anchor(block['id']))
            end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(bookmark))
            # Attach to the next heading without an extra blank line.
            p=doc.add_paragraph();p.paragraph_format.space_after=Pt(0);p.paragraph_format.space_before=Pt(0)
            p.paragraph_format.line_spacing=Pt(1);p.paragraph_format.keep_with_next=True
            p._p.append(start);p._p.append(end)
        elif kind=='citations':
            p=doc.add_paragraph('근거: ','Caption')
            if not block['ids']:p.add_run('직접 인용 근거 없음')
            for i,oid in enumerate(block['ids']):
                if i:p.add_run(' · ')
                link=OxmlElement('w:hyperlink');link.set(qn('w:anchor'),anchor(oid))
                run=OxmlElement('w:r');t=OxmlElement('w:t');t.text=oid;run.append(t);link.append(run);p._p.append(link)
        elif kind=='table':
            if not block['rows']:continue
            table=doc.add_table(rows=1,cols=len(block['headers']));table.style='Table Grid';table.autofit=False
            borders=OxmlElement('w:tblBorders')
            for side in ('top','left','bottom','right','insideH','insideV'):
                edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'single')
                edge.set(qn('w:sz'),'4');edge.set(qn('w:color'),'D9D9D9');borders.append(edge)
            table._tbl.tblPr.append(borders)
            margins=OxmlElement('w:tblCellMar')
            for side,value in (('top','50'),('bottom','50'),('left','100'),('right','100')):
                edge=OxmlElement('w:'+side);edge.set(qn('w:w'),value);edge.set(qn('w:type'),'dxa');margins.append(edge)
            table._tbl.tblPr.append(margins)
            widths=block.get('widths') or [170/len(block['headers'])]*len(block['headers'])
            for col,width in zip(table.columns,widths):col.width=Mm(width)
            for cell,width,label in zip(table.rows[0].cells,widths,block['headers']):
                cell.width=Mm(width);cell.text=label
                shading=OxmlElement('w:shd');shading.set(qn('w:fill'),'EDEDED');cell._tc.get_or_add_tcPr().append(shading)
                for run in cell.paragraphs[0].runs:run.bold=True
            repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for row in block['rows']:
                cells=table.add_row().cells
                for cell,width,value in zip(cells,widths,row):cell.width=Mm(width);cell.text=value
            for row in table.rows:
                for index,cell in enumerate(row.cells):
                    cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    for p in cell.paragraphs:
                        if block['headers'][index] in ('등급','판단','상태','발견','읽기','보존','파싱','미처리','판단 ID'):
                            p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                        p.paragraph_format.space_after=Pt(3);p.paragraph_format.space_before=Pt(3)
                        p.paragraph_format.line_spacing=1.15
                        for run in p.runs:run.font.size=Pt(9.5)
                    # Permit long unbroken IDs/paths to wrap inside fixed columns.
                    for p in cell.paragraphs:
                        wrap=OxmlElement('w:wordWrap');wrap.set(qn('w:val'),'1');p._p.get_or_add_pPr().append(wrap)
            doc.add_paragraph().paragraph_format.space_after=Pt(0)
    doc.add_paragraph(view['redaction_notice'],'Caption')
    stream=io.BytesIO();doc.save(stream);result=stream.getvalue()
    validate(result)
    return result


def validate(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip():raise ValueError('Word 파일 CRC 검증 실패')
        for name in archive.namelist():
            if name.endswith(('.xml','.rels')):
                root=ElementTree.fromstring(archive.read(name))
                if any(x.attrib.get('TargetMode')=='External' for x in root.iter()):
                    raise ValueError('배포 Word에 외부 리소스가 포함되었습니다.')
