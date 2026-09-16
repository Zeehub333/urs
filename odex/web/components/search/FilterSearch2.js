// search/FilterSearch2 — Material FilterSearch distinct v2
// unique: with filter chips — hash 4a84
export class FilterSearch2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search filtersearch2 filtersearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">FilterSearch — with filter chips (4a8423)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with filter chips'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with filter chips — '+q+'</div></div>'; }
}