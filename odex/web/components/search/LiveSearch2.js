// search/LiveSearch2 — Material LiveSearch distinct v2
// unique: with filter chips — hash 6881
export class LiveSearch2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search livesearch2 livesearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">LiveSearch — with filter chips (68814c)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with filter chips'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with filter chips — '+q+'</div></div>'; }
}