// tables/EditableTable8 — Material EditableTable distinct v8
export class EditableTable8 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):[]); var h=cols.map(c=>'<th>'+c+'</th>').join(''); return '<div class="md-table-container editabletable8" style="max-height:120px;overflow:auto"><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'; }
}