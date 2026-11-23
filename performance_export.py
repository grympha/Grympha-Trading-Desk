"""Downloadable reports for the same confirmed closed trades as Trade Review."""
import csv
import io
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

SUMMARY = ('Pair','Currency','Trades','TP','BE','SL','Other','Wins','Losses','Flat','Unknown','Win rate %','Net P/L')
DETAILS = ('Entry (server)','Exit (server)','Strategy','Pair','Side','SL','TP','BE','Exit reason','Outcome','Net P/L','Position ticket')


def server_time(epoch):
    """MT5 server (broker) wall clock. Broker deal epochs are already stored as server time,
    so reading them in UTC reproduces the broker clock without a second offset."""
    if epoch in (None,'') or not isinstance(epoch,(int,float)):
        return '-'
    return datetime.fromtimestamp(epoch,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def price(value):
    return ('%g' % value) if isinstance(value,(int,float)) else '-'


def report_rows(report):
    summaries = []
    for p in report['pairs']:
        known = p['wins'] + p['losses'] + p['flat']
        summaries.append([p['symbol'],p['currency'],p['total'],p['tp'],p.get('be',0),p['sl'],p['other'],
                          p['wins'],p['losses'],p['flat'],p['unknown'],
                          round(100*p['wins']/known,2) if known else None,
                          round(p['net'],2) if p['net'] is not None else None])
    details = [
        [server_time(r.get('entry_epoch')),server_time(r.get('exit_epoch') or r.get('closed_epoch')),
         r['strategy'],r['symbol'],r.get('side'),price(r.get('sl')),price(r.get('tp1')),
         'Yes' if r.get('be') else '',r['exit_reason'],r['outcome'],
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
    metadata = [('Strategy',strategy),('Pair',symbol),('From',start or 'Any'),
                ('To',end or 'Any'),('Trades closed',report['total']),('Report status',report['status']),
                ('Times','MT5 server time')]
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
        ('Performance by pair',SUMMARY,summaries,[74,56,46,34,34,34,42,42,46,40,54,64,84]),
        ('Closed trades',DETAILS,details,[92,92,58,52,36,52,52,30,92,56,56,58]),
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
        canvas.drawString(30,17,'Closed bot trades  |  MT5 server time')
        canvas.drawRightString(landscape(A4)[0]-30,17,str(document.page))

    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
