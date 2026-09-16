// tables/DataTable1 — Material DataTable distinct v1
export class DataTable1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):['المعرف']); var h=cols.map(c=>'<th>'+c+'</th>').join(''); var b=rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join(''); return '<div class="md-table-container datatable1"><div style="font-size:11px;color:var(--md-primary);padding:4px">DataTable — basic</div><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table></div>'; }
}