// tables/VirtualTable8 — Material VirtualTable distinct v8
export class VirtualTable8 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):[]); var h=cols.map(c=>'<th>'+c+'</th>').join(''); return '<div class="md-table-container virtualtable8" style="max-height:120px;overflow:auto"><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'; }
}