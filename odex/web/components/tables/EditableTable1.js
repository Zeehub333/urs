// tables/EditableTable1 — Material EditableTable distinct v1
export class EditableTable1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):['المعرف']); var h=cols.map(c=>'<th>'+c+'</th>').join(''); var b=rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join(''); return '<div class="md-table-container editabletable1"><div style="font-size:11px;color:var(--md-primary);padding:4px">EditableTable — basic</div><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table></div>'; }
}