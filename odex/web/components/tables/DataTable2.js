// tables/DataTable2 — Material DataTable distinct v2
export class DataTable2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):[]); var h=cols.map(c=>'<th style="cursor:pointer" onclick="alert(\'sort '+c+'\')">'+c+' ↕</th>').join(''); var b=rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join(''); return '<div class="md-table-container datatable2"><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table></div>'; }
}