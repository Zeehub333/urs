// search/LiveSearch3 — Material LiveSearch distinct v3
// unique: with autocomplete — hash b1a9
export class LiveSearch3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search livesearch3 livesearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">LiveSearch — with autocomplete (b1a9b1)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with autocomplete'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with autocomplete — '+q+'</div></div>'; }
}