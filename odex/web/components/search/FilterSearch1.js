// search/FilterSearch1 — Material FilterSearch distinct v1
// unique: basic — hash 78e9
export class FilterSearch1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search filtersearch1 filtersearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">FilterSearch — basic (78e93a)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'basic'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">basic — '+q+'</div></div>'; }
}