// search/SearchBar4 — Material SearchBar distinct v4
// unique: with mic — hash 3c16
export class SearchBar4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search searchbar4 searchbar"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">SearchBar — with mic (3c162a)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with mic'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with mic — '+q+'</div></div>'; }
}