// fields/PasswordField5 — Material PasswordField distinct v5
// unique: with prefix/suffix — hash d8e3
export class PasswordField5 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field passwordfield5"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'PasswordField5')+' — with prefix/suffix</label><div style="font-size:11px;color:#666">'+(cfg.helper||'with prefix/suffix')+'</div></div>'; }
}