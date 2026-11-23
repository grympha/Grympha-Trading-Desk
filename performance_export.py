"""Downloadable reports for the same confirmed closed trades as Trade Review."""
import csv
import io
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

MYT = timezone(timedelta(hours=8))
SUMMARY = ('Pair','Currency','Trades','TP','SL','Other','Wins','Losses','Flat','Unknown','Win rate %','Net P/L')
DETAILS = ('Closed (MYT)','Strategy','Pair','Setup','Side','Exit reason','Outcome','Currency','Net P/L','Position ticket')


def report_rows(report):
    summaries = []
    for p in report['pairs']:
        known = p['wins'] + p['losses'] + p['flat']
        summaries.append([p['symbol'],p['currency'],p['total'],p['tp'],p['sl'],p['other'],
                          p['wins'],p['losses'],p['flat'],p['unknown'],
                          round(100*p['wins']/known,2) if known else None,
                          round(p['net'],2) if p['net'] is not None else None])
    details = [
        [datetime.fromtimestamp(r['closed_epoch'],MYT).strftime('%Y-%m-%d %H:%M:%S'),
         r['strategy'],r['symbol'],r.get('setup'),r.get('side'),r['exit_reason'],r['outcome'],r['currency'],
         round(r['realised'],2) if r['realised'] is not None else None,r.get('position_ticket')]
        for r in report['reviews']
    ]
    return summaries, details


def csv_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
        return "'"+value
    return value


def render(report, filters, kind):
    strategy,symbol,start,end = filters
    metadata = [('Strategy',strategy),('Pair',symbol),('From (MYT)',start or 'Any'),
                ('To (MYT)',end or 'Any'),('Trades closed',report['total']),('Report status',report['status'])]
    summaries,details = report_rows(report)
    if kind == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        for row in [('Grympha Trading Desk - Performance Report',),*metadata,(),SUMMARY,*summaries,(),DETAILS,*details]:
            writer.writerow([csv_cell(value) for value in row])
        return output.getvalue().encode('utf-8-sig')

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    output = io.BytesIO()
    doc = SimpleDocTemplate(output,pagesize=landscape(A4),leftMargin=30,rightMargin=30,
                            topMargin=32,bottomMargin=32,title='Grympha Trading Desk - Performance Report')
    styles = getSampleStyleSheet()
    styles['BodyText'].fontSize = 8
    styles['BodyText'].leading = 10
    story = [Paragraph('Performance Report',styles['Title']),
             Paragraph('Grympha Trading Desk',styles['Heading2']),
             Paragraph(escape(' | '.join(f'{key}: {value}' for key,value in metadata)),styles['BodyText']),
             Spacer(1,12)]
    for title,head,rows,widths in [
        ('Performance by pair',SUMMARY,summaries,[80,60,50,42,42,44,45,49,44,60,70,90]),
        ('Closed trades',DETAILS,details,[120,80,66,105,43,94,62,48,58,65]),
    ]:
        story.append(Paragraph(title,styles['Heading2']))
        if not rows:
            story.append(Paragraph('No matching closed trades available.',styles['BodyText']))
            continue
        cells = [[Paragraph(escape(str(value) if value is not None else '-'),styles['BodyText'])
                  for value in row] for row in [head,*rows]]
        table = LongTable(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DDE8F2')),
                                  ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F3F6F9')]),
                                  ('VALIGN',(0,0),(-1,-1),'TOP')]))
        story.extend([table,Spacer(1,12)])

    def footer(canvas, document):
        canvas.setFont('Helvetica',8)
        canvas.drawString(30,17,'Closed bot trades  |  MYT (UTC+8)')
        canvas.drawRightString(landscape(A4)[0]-30,17,str(document.page))

    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
