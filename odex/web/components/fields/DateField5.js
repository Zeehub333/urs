// fields/DateField5 — Material DateField distinct v5
// unique: with prefix/suffix — hash e174
export class DateField5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field datefield5"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'DateField5')+' — with prefix/suffix</label><div style="font-size:11px;color:#666">'+(cfg.helper||'with prefix/suffix')+'</div></div>'; }
}