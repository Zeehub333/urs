// tables/VirtualTable5 — Material VirtualTable distinct v5
export class VirtualTable5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):[]); var h=cols.map(c=>'<th>'+c+'</th>').join(''); var b=rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join(''); return '<div class="md-table-container virtualtable5"><div style="font-size:10px;color:#666;padding:4px">VirtualTable — expandable rows</div><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table></div>'; }
}