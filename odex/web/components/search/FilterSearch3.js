// search/FilterSearch3 — Material FilterSearch distinct v3
// unique: with autocomplete — hash dfb4
export class FilterSearch3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search filtersearch3 filtersearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">FilterSearch — with autocomplete (dfb46a)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with autocomplete'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with autocomplete — '+q+'</div></div>'; }
}