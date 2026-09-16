// fields/CheckboxField5 — Material CheckboxField distinct v5
// unique: with prefix/suffix — hash 4b42
export class CheckboxField5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field checkboxfield5"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'CheckboxField5')+' — with prefix/suffix</label><div style="font-size:11px;color:#666">'+(cfg.helper||'with prefix/suffix')+'</div></div>'; }
}