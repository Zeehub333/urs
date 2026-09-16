// fields/SelectField3 — Material SelectField distinct v3
// unique: with helper text — hash fbde
export class SelectField3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field selectfield3"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'SelectField3')+' — with helper text</label><div style="font-size:11px;color:#666">'+(cfg.helper||'with helper text')+'</div></div>'; }
}