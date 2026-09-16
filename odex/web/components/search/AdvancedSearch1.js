// search/AdvancedSearch1 — Material AdvancedSearch distinct v1
// unique: basic — hash 0031
export class AdvancedSearch1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search advancedsearch1 advancedsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">AdvancedSearch — basic (003184)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'basic'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">basic — '+q+'</div></div>'; }
}