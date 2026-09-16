// fields/DateField3 — Material DateField distinct v3
// unique: with helper text — hash aecd
export class DateField3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field datefield3"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'DateField3')+' — with helper text</label><div style="font-size:11px;color:#666">'+(cfg.helper||'with helper text')+'</div></div>'; }
}