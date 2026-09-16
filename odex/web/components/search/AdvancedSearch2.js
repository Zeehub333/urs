// search/AdvancedSearch2 — Material AdvancedSearch distinct v2
// unique: with filter chips — hash 5e59
export class AdvancedSearch2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search advancedsearch2 advancedsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">AdvancedSearch — with filter chips (5e5945)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with filter chips'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with filter chips — '+q+'</div></div>'; }
}