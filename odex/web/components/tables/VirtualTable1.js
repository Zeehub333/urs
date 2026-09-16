// tables/VirtualTable1 — Material VirtualTable distinct v1
export class VirtualTable1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):['المعرف']); var h=cols.map(c=>'<th>'+c+'</th>').join(''); var b=rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join(''); return '<div class="md-table-container virtualtable1"><div style="font-size:11px;color:var(--md-primary);padding:4px">VirtualTable — basic</div><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table></div>'; }
}