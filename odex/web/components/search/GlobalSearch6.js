// search/GlobalSearch6 — Material GlobalSearch distinct v6
// unique: advanced grid — hash d472
export class GlobalSearch6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search globalsearch6 globalsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">GlobalSearch — advanced grid (d47263)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'advanced grid'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">advanced grid — '+q+'</div></div>'; }
}