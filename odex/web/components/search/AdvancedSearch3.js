// search/AdvancedSearch3 — Material AdvancedSearch distinct v3
// unique: with autocomplete — hash 2860
export class AdvancedSearch3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search advancedsearch3 advancedsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">AdvancedSearch — with autocomplete (286021)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with autocomplete'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with autocomplete — '+q+'</div></div>'; }
}