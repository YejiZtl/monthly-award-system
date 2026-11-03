"""Meeting rows use the same template labels and approved components as Excel export."""
from decimal import Decimal
from app.services.summary_review import template_rows, FIXED_ROWS


def meeting_rows(repository, template_path, month, snapshot, remarks):
    notes = {r['key']:r['text'] for r in remarks}
    labels = template_rows(template_path)
    rows = [{'key':f'fixed:{n}', 'number':n-2, 'label':labels[n]['label'],
             'equipment':None,'technical':None,'profit':None,'total':None,
             'introducer':'','owner':'','remark':notes.get(f'fixed:{n}',''),
             'remark_key':f'fixed:{n}', 'batch_id':None,'calculation_id':None}
            for n in range(3,20)]
    # Same order as export, for project rows; the digest is deliberately order-independent.
    for calc in repository.list_confirmed_calculations(month):
        item = next((i for i in snapshot['items'] if i['calculation_id']==calc['id']), None)
        if item is None:
            # The API rechecks the digest after collecting rows and returns a conflict.
            continue
        fields = {f['path']:f['value_normalized'] for f in repository.list_fields(item['batch_id'])}
        fixed = FIXED_ROWS.get(item['form_type'])
        if fixed:
            row = rows[fixed-3]
        else:
            row = {'key':f'batch:{item["batch_id"]}', 'number':len(rows)+1,
                   'label':item['project_name'] or item['batch_no'], 'remark_key':f'batch:{item["batch_id"]}'}
            rows.append(row)
        components = (calc.get('metadata') or {}).get('summary_components') or {}
        row.update({name: str(Decimal(str(components[name])).quantize(Decimal('.01'))) if name in components else None
                    for name in ('equipment','technical','profit')})
        row.update(total=item['total'],batch_id=item['batch_id'],calculation_id=calc['id'],
                   owner=fields.get('project.owner') or item['project_owner'] or '',
                   introducer=fields.get('project.introducer') or '', remark=notes.get(row['remark_key'],''))
    manual_rows = {f'fixed:{n}' for n in range(3,20) if n not in FIXED_ROWS.values()}
    rows = [row for row in rows if row['key'] not in snapshot.get('excluded_keys',[])]
    for row in rows:
        row['manual_total'] = row['key'] in manual_rows
        extra = snapshot.get('cells',{}).get(row['key'],{})
        row.update(snapshot['effective_amounts'][row['key']])
        for name in ('weight','actual'):
            row[name] = extra.get(name)
        if row['actual'] is None:
            row['actual'] = row['total']
        row['reason'] = snapshot.get('cell_reasons',{}).get(row['key'],'')
    for supplemental in snapshot.get('supplemental_rows',[]):
        rows.append(dict(supplemental, key='supplement:'+supplemental['id'], number=len(rows)+1,
                         supplemental=True, manual_total=True, total=supplemental['total'],
                         equipment=None, technical=None, profit=None, actual=supplemental['actual'], weight=None,
                         batch_id=None, calculation_id=None, owner='', introducer=''))
    for number, row in enumerate(rows, 1):
        row['number'] = number
    return rows
