// tables/EditableTable4 — Material EditableTable distinct v4
export class EditableTable4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var rows=p.rows||[]; var cols=p.cols|| (rows[0]?Object.keys(rows[0]):[]); var h=cols.map(c=>'<th>'+c+'</th>').join(''); var b=rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]||'')+'</td>').join('')+'</tr>').join(''); return '<div class="md-table-container editabletable4"><div style="font-size:10px;color:#666;padding:4px">EditableTable — paginated</div><table class="md-table"><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table></div>'; }
}