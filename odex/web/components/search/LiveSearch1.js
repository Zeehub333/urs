// search/LiveSearch1 — Material LiveSearch distinct v1
// unique: basic — hash 8f0b
export class LiveSearch1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search livesearch1 livesearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">LiveSearch — basic (8f0bb9)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'basic'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">basic — '+q+'</div></div>'; }
}