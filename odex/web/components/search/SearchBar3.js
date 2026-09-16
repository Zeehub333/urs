// search/SearchBar3 — Material SearchBar distinct v3
// unique: with autocomplete — hash 9ae2
export class SearchBar3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search searchbar3 searchbar"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">SearchBar — with autocomplete (9ae2f7)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with autocomplete'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with autocomplete — '+q+'</div></div>'; }
}