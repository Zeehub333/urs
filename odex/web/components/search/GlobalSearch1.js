// search/GlobalSearch1 — Material GlobalSearch distinct v1
// unique: basic — hash 2f8d
export class GlobalSearch1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search globalsearch1 globalsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">GlobalSearch — basic (2f8d5d)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'basic'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">basic — '+q+'</div></div>'; }
}