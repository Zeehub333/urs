// search/GlobalSearch3 — Material GlobalSearch distinct v3
// unique: with autocomplete — hash 96f1
export class GlobalSearch3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search globalsearch3 globalsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">GlobalSearch — with autocomplete (96f162)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with autocomplete'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with autocomplete — '+q+'</div></div>'; }
}