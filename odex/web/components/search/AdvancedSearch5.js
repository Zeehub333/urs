// search/AdvancedSearch5 — Material AdvancedSearch distinct v5
// unique: with history — hash a848
export class AdvancedSearch5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var q=p.query||''; return '<div class="md-card search advancedsearch5 advancedsearch"><div style="font-size:11px;color:var(--md-primary);margin-bottom:4px">AdvancedSearch — with history (a84852)</div><div style="display:flex;gap:8px"><input value="'+q+'" placeholder="'+'with history'+'" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:999px"/><button class="md-btn md-btn-primary">بحث</button></div><div style="font-size:11px;color:#666;margin-top:4px">with history — '+q+'</div></div>'; }
}