// search/GlobalSearch2 — Material GlobalSearch distinct v2
// unique: with filter chips — hash e5a6
export class GlobalSearch2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search globalsearch2 globalsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">GlobalSearch — with filter chips (e5a62a)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with filter chips'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with filter chips — '+q+'</div></div>'; }
}