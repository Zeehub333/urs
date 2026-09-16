// search/LiveSearch6 — Material LiveSearch distinct v6
// unique: advanced grid — hash 9ef2
export class LiveSearch6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search livesearch6 livesearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">LiveSearch — advanced grid (9ef278)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'advanced grid'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">advanced grid — '+q+'</div></div>'; }
}