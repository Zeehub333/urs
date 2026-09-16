// search/SearchBar7 — Material SearchBar distinct v7
// unique: live results — hash 55d9
export class SearchBar7 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search searchbar7 searchbar"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">SearchBar — live results (55d9d9)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'live results'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">live results — '+q+'</div></div>'; }
}