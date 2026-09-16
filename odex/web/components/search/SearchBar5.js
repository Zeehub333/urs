// search/SearchBar5 — Material SearchBar distinct v5
// unique: with history — hash 15b0
export class SearchBar5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search searchbar5 searchbar"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">SearchBar — with history (15b018)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with history'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with history — '+q+'</div></div>'; }
}