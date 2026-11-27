"""Windows desktop launcher; stdlib-only GUI. Start via START_APP.cmd."""
import ctypes,json,os,queue,shutil,signal,socket,subprocess,sys,threading,time,webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from launcher_extras import (ICON_FILE,TrayIcon,load_settings,save_settings,read_real_account,save_real_account,
                             validate_real_account,startup_enabled,set_startup,show_console,write_icon,clean_desk_name)
import app_updater
STARTED_BY_WINDOWS='--startup' in sys.argv
ROOT=Path(__file__).resolve().parent
VENV=ROOT/'.venv'
PYTHON_GUIDE='https://www.python.org/downloads/release/python-3119/'

def backup(path):
    if path.exists():shutil.copy2(path,path.with_name(path.name+'.backup.'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')))

def discover_terminals():
    candidates=[]
    for env in ('ProgramFiles','ProgramFiles(x86)'):
        root=Path(os.environ.get(env,'C:/Program Files'))
        if root.exists():candidates.extend(root.glob('*/terminal64.exe'))
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\Uninstall',0,winreg.KEY_READ|view) as base:
                        for i in range(winreg.QueryInfoKey(base)[0]):
                            try:
                                with winreg.OpenKey(base,winreg.EnumKey(base,i)) as key:
                                    location=winreg.QueryValueEx(key,'InstallLocation')[0]
                                    p=Path(location)/'terminal64.exe'
                                    if p.is_file():candidates.append(p)
                            except OSError:pass
                except OSError:pass
    except ImportError:pass
    return sorted({str(p.resolve()) for p in candidates if p.is_file()})

class App:
    def __init__(self,root):
        self.root=root;self.events=queue.Queue();self.busy=False;self.bot=None;self.report=None;self.closing=False;self.lock=None;self.running_port=None
        self.acquire_lock()
        self.settings=load_settings();self.tray=None;self.tray_hint_shown=False;self.auto_tries=0
        self.stop_requested=False;self.last_run=None;self.restart_times=[];self.restarting=False;self.restart_pending=False
        self.update_busy=False;self.update_info=None;self.update_restart_pending=False;self.update_gen=0;self.failed_target=None
        root.title(self.window_title());root.geometry('1160x1010');root.minsize(900,760)
        try:
            if not ICON_FILE.exists():write_icon(ICON_FILE)
            root.iconbitmap(default=str(ICON_FILE))
        except Exception:pass
        style=ttk.Style();style.theme_use('clam')
        ttk.Label(root,text='Automated Trading Agent',font=('Segoe UI',19,'bold')).pack(anchor='w',padx=18,pady=(15,4))
        ttk.Label(root,text='Checks and setup first. Default start mode sends no orders. Keep MT5 logged in on this PC.').pack(anchor='w',padx=18)
        self.build_desk_name(root)
        bar=ttk.Frame(root);bar.pack(fill='x',padx=15,pady=10)
        self.buttons=[]
        actions=[('Check requirements',self.check),('Setup / Install requirements',self.setup),('Choose MT5 terminal',self.choose_terminal),('Open MT5',self.open_terminal),('Install Calendar EA',self.install_ea),('Open app folder',lambda:os.startfile(ROOT))]
        for i,(label,fn) in enumerate(actions):
            b=ttk.Button(bar,text=label,command=fn);b.grid(row=i//3,column=i%3,padx=4,pady=4,sticky='ew');self.buttons.append(b)
        for i in range(3):bar.columnconfigure(i,weight=1)
        tools=ttk.Frame(root);tools.pack(fill='x',padx=18)
        for label,name in [('Configure 9 pairs','configure_major_pairs.py'),('Enable MT5 news','configure_v1_8.py'),('Set clock offset','configure_clock_offset.py'),('Calendar diagnostics','diagnose_mt5_calendar.py'),('Time diagnostics','--diagnose-time')]:
            b=ttk.Button(tools,text=label,command=lambda n=name:self.script(n));b.pack(side='left',padx=3,pady=4);self.buttons.append(b)
        ttk.Button(tools,text='Setup guide',command=self.guide).pack(side='right',padx=3)
        frame=ttk.Frame(root);frame.pack(fill='both',expand=True,padx=18,pady=8)
        self.table=ttk.Treeview(frame,columns=('name','status','detail'),show='headings',height=10)
        for k,text,width in [('name','Requirement',200),('status','Status',80),('detail','Result / action',730)]:self.table.heading(k,text=text);self.table.column(k,width=width)
        self.table.tag_configure('FAIL',foreground='#b12525');self.table.tag_configure('PASS',foreground='#06734c');self.table.tag_configure('WARN',foreground='#916000');self.table.tag_configure('CLOSED',foreground='#5b6b7d')
        self.table.pack(fill='both',expand=True);self.table.bind('<<TreeviewSelect>>',self.show_row)
        self.help=tk.StringVar(value='Select a requirement row to see steps. First installation: click Setup / Install requirements.')
        ttk.Label(root,textvariable=self.help,wraplength=1100).pack(fill='x',padx=18,pady=4)
        run=ttk.Frame(root);run.pack(fill='x',padx=18,pady=8)
        self.mode=tk.StringVar(value=self.settings['last_mode'])
        ttk.Radiobutton(run,text='Analysis only (no orders)',variable=self.mode,value='analysis').pack(side='left')
        ttk.Radiobutton(run,text='DEMO execution (new orders)',variable=self.mode,value='demo').pack(side='left',padx=12)
        ttk.Radiobutton(run,text='REAL execution',variable=self.mode,value='real').pack(side='left',padx=8)
        ttk.Label(run,text='Port').pack(side='left');self.port=tk.StringVar(value=str(self.settings.get('port',8765)));ttk.Spinbox(run,from_=1024,to=65535,textvariable=self.port,width=7).pack(side='left',padx=4)
        self.start_button=ttk.Button(run,text='Start bot',command=self.start);self.start_button.pack(side='left',padx=5);self.buttons.append(self.start_button)
        self.stop_button=ttk.Button(run,text='Stop bot',command=self.stop,state='disabled');self.stop_button.pack(side='left',padx=3)
        self.restart_button=ttk.Button(run,text='Restart bot',command=self.restart_bot,state='disabled');self.restart_button.pack(side='left',padx=3)
        ttk.Button(run,text='Open dashboard',command=self.open_dashboard).pack(side='left',padx=3)
        self.build_real_account(root);self.build_telegram(root);self.build_windows_options(root);self.build_updates(root)
        self.status=tk.StringVar(value='Ready · no bot started');ttk.Label(root,textvariable=self.status).pack(anchor='w',padx=18)
        self.log=tk.Text(root,height=9,font=('Consolas',9),wrap='word');self.log.pack(fill='both',padx=18,pady=(4,14))
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.drain);root.after(400,self.check)
        root.bind('<Unmap>',self.on_unmap)
        self.start_tray()
        if STARTED_BY_WINDOWS and self.settings['start_in_tray'] and self.tray:root.after(300,self.to_tray)
        if self.settings['autostart_bot']:root.after(20000 if STARTED_BY_WINDOWS else 3000,self.auto_start)
        if STARTED_BY_WINDOWS:root.after(1500,self.startup_notice)
        if self.settings['update_auto_check']:root.after(90000 if STARTED_BY_WINDOWS else 45000,self.update_tick)
    # ---------------------------------------------------------------- desk name (several desks on one PC)
    def desk_label(self):
        """The chosen desk name; otherwise this folder's name when it is not the default one."""
        name=self.settings.get('desk_name') or ''
        return name or ('' if ROOT.name=='Grympha Trading Desk' else ROOT.name)
    def window_title(self):
        name=self.desk_label()
        return (name+' — ' if name else '')+'Grympha Trading Desk · Automated Trading Agent · v1.11'
    def tip(self,text):
        name=self.desk_label()
        return (name+' · ' if name else '')+'Grympha Trading Desk · '+text
    def build_desk_name(self,root):
        row=ttk.Frame(root);row.pack(fill='x',padx=18,pady=(6,0))
        ttk.Label(row,text='Desk name').pack(side='left')
        self.desk_var=tk.StringVar(value=self.settings.get('desk_name',''))
        e=ttk.Entry(row,textvariable=self.desk_var,width=30);e.pack(side='left',padx=6);e.bind('<Return>',lambda _:self.save_desk_name())
        ttk.Button(row,text='Save',command=self.save_desk_name).pack(side='left')
        ttk.Label(row,text='e.g. "Main · Demo" or "Account 2 · Real". Shown in this window, the dashboard, the home page and Telegram alerts, so two desks on one PC are easy to tell apart.',foreground='#4a5d73').pack(side='left',padx=10)
    def save_desk_name(self):
        name=clean_desk_name(self.desk_var.get());self.desk_var.set(name)
        self.settings['desk_name']=name
        try:save_settings(self.settings)
        except Exception as e:messagebox.showerror('Desk name','Not saved: '+str(e));return
        self.root.title(self.window_title())
        if self.tray:self.tray.set_tooltip(self.tip('bot running' if self.bot_running() else 'ready'))
        self.write(('Desk name saved: '+name if name else 'Desk name cleared')+'. The dashboard, home page and Telegram alerts use it straight away; no restart needed.')
    # ---------------------------------------------------------------- REAL execution account
    def build_real_account(self,root):
        box=ttk.LabelFrame(root,text='REAL execution account (used only in REAL mode; must match the account logged into MT5)')
        box.pack(fill='x',padx=18,pady=(2,4))
        login,server=read_real_account(ROOT/'config.json')
        self.real_login=tk.StringVar(value='' if login is None else str(login));self.real_server=tk.StringVar(value=server)
        ttk.Label(box,text='Account number').pack(side='left',padx=(8,4),pady=6)
        ttk.Entry(box,textvariable=self.real_login,width=14).pack(side='left')
        ttk.Label(box,text='Server').pack(side='left',padx=(12,4))
        ttk.Entry(box,textvariable=self.real_server,width=24).pack(side='left')
        self.fill_button=ttk.Button(box,text='Use logged-in MT5 account',command=self.fill_real_from_mt5);self.fill_button.pack(side='left',padx=(10,3))
        ttk.Button(box,text='Save REAL account',command=self.save_real).pack(side='left',padx=3)
        self.real_state=tk.StringVar(value=self.real_summary(login,server));ttk.Label(box,textvariable=self.real_state,foreground='#4a5d73').pack(side='left',padx=10)
    def real_summary(self,login,server):
        return f'Saved: {login} @ {server}' if login and server else 'Not set yet · REAL mode stays blocked until saved'
    def fill_real_from_mt5(self):
        self.fill_button.configure(state='disabled');self.real_state.set('Reading the account logged into MT5…')
        code=('import json,sys,MetaTrader5 as m\n'
              'cfg=json.load(open("config.json",encoding="utf-8-sig"));p=cfg.get("terminal_path")\n'
              'ok=m.initialize(path=p) if p else m.initialize()\n'
              'a=m.account_info() if ok else None\n'
              'print(json.dumps(None if a is None else {"login":a.login,"server":a.server,"trade_mode":a.trade_mode}));m.shutdown()')
        def work():
            try:
                r=subprocess.run([self.python(),'-c',code],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
                info=json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode==0 and r.stdout.strip() else None
                self.events.put(('mt5_account',info or {'error':'MT5 not connected. Open MT5, log in, then try again.'}))
            except Exception as exc:self.events.put(('mt5_account',{'error':'Could not read MT5: '+str(exc)[:160]}))
        threading.Thread(target=work,daemon=True).start()
    def save_real(self):
        try:login,server=validate_real_account(self.real_login.get(),self.real_server.get())
        except ValueError as e:messagebox.showerror('REAL account',str(e));return
        if not (ROOT/'config.json').exists():messagebox.showerror('REAL account','Run Setup first to create config.json.');return
        if not messagebox.askyesno('Save REAL account',f'Save REAL execution account {login} on server {server}?\n\n'
            'REAL mode will only place orders when MT5 is logged into exactly this account. A backup of config.json is kept.',default='no'):return
        try:backup=save_real_account(ROOT/'config.json',login,server)
        except Exception as e:messagebox.showerror('REAL account','Not saved: '+str(e));return
        self.real_state.set(self.real_summary(login,server));self.write(f'REAL account saved ({login} @ {server}). Backup: {backup.name}')
        if self.bot and self.bot.poll() is None:self.write('The running bot keeps its old settings until it is restarted.')
    # ---------------------------------------------------------------- Telegram
    def build_telegram(self,root):
        import telegram_config as tg
        c=tg.read()
        box=ttk.LabelFrame(root,text='Telegram alerts (saved in .env on this PC; which events to send: dashboard → Settings → Telegram notifications)')
        box.pack(fill='x',padx=18,pady=(2,4))
        self.tg_enabled=tk.BooleanVar(value=c['enabled']);self.tg_token=tk.StringVar(value=c['token']);self.tg_chat=tk.StringVar(value=c['chat'])
        ttk.Checkbutton(box,text='Enable',variable=self.tg_enabled).pack(side='left',padx=(8,8),pady=6)
        ttk.Label(box,text='Bot token').pack(side='left',padx=(0,4))
        self.tg_token_entry=ttk.Entry(box,textvariable=self.tg_token,width=30,show='•');self.tg_token_entry.pack(side='left')
        self.tg_show=tk.BooleanVar(value=False)
        ttk.Checkbutton(box,text='Show',variable=self.tg_show,command=lambda:self.tg_token_entry.configure(show='' if self.tg_show.get() else '•')).pack(side='left',padx=(2,8))
        ttk.Label(box,text='Chat ID').pack(side='left',padx=(0,4))
        ttk.Entry(box,textvariable=self.tg_chat,width=15).pack(side='left')
        self.tg_buttons=[ttk.Button(box,text='Find my chat ID',command=self.tg_find),ttk.Button(box,text='Save',command=self.tg_save),ttk.Button(box,text='Send test',command=self.tg_test)]
        for b in self.tg_buttons:b.pack(side='left',padx=3)
        self.tg_state=tk.StringVar(value=self.tg_summary(c));ttk.Label(box,textvariable=self.tg_state,foreground='#4a5d73').pack(side='left',padx=8)
    def tg_summary(self,c):
        import telegram_config as tg
        if not c['configured']:return 'Not set up · create a bot with @BotFather'
        return ('ON' if c['enabled'] else 'OFF')+' · '+tg.mask(c['token'])+' → '+c['chat']
    def tg_busy(self,busy):
        for b in self.tg_buttons:b.configure(state='disabled' if busy else 'normal')
    def tg_background(self,label,fn):
        self.tg_busy(True);self.tg_state.set(label)
        def work():
            try:self.events.put(('telegram',fn()))
            except Exception as exc:self.events.put(('telegram',dict(error=str(exc)[:200])))
        threading.Thread(target=work,daemon=True).start()
    def tg_find(self):
        import telegram_config as tg
        token=self.tg_token.get().strip()
        try:tg.validate(token,'')
        except ValueError as e:messagebox.showerror('Telegram',str(e));return
        if not token:messagebox.showinfo('Telegram','Paste your bot token first (from @BotFather).');return
        self.tg_background('Looking for chats… (send /start to your bot in Telegram first)',lambda:dict(chats=tg.find_chats(token)))
    def tg_test(self):
        import telegram_config as tg
        token,chat=self.tg_token.get().strip(),self.tg_chat.get().strip()
        try:tg.validate(token,chat)
        except ValueError as e:messagebox.showerror('Telegram',str(e));return
        if not (token and chat):messagebox.showinfo('Telegram','Enter the bot token and chat ID first.');return
        self.tg_background('Sending test message…',lambda:dict(sent=tg.send(token,chat,(f'[{self.settings["desk_name"]}] ' if self.settings.get('desk_name') else '')+'✅ Grympha Trading Desk: Telegram test message. Alerts will arrive here. No trade was placed.')))
    def tg_save(self):
        import telegram_config as tg
        try:backup=tg.write(self.tg_enabled.get(),self.tg_token.get(),self.tg_chat.get())
        except ValueError as e:messagebox.showerror('Telegram',str(e));return
        except OSError as e:messagebox.showerror('Telegram','Could not write .env: '+str(e));return
        c=tg.read();self.tg_state.set(self.tg_summary(c)+' · saved')
        self.write('Telegram settings saved to .env ('+('enabled' if c['enabled'] else 'disabled')+')'+(f'. Backup: {backup.name}' if backup else '')+'. The running bot picks this up on its next message; no restart needed.')
    # ---------------------------------------------------------------- Windows start-up and tray options
    def build_windows_options(self,root):
        box=ttk.LabelFrame(root,text='Windows');box.pack(fill='x',padx=18,pady=(2,4))
        self.opt_startup=tk.BooleanVar(value=startup_enabled());self.opt_tray=tk.BooleanVar(value=self.settings['minimize_to_tray'])
        self.opt_start_tray=tk.BooleanVar(value=self.settings['start_in_tray']);self.opt_autobot=tk.BooleanVar(value=self.settings['autostart_bot'])
        self.opt_automode=tk.StringVar(value={'analysis':'Analysis only','demo':'DEMO execution','real':'REAL execution'}[self.settings['autostart_mode']])
        ttk.Checkbutton(box,text='Start with Windows',variable=self.opt_startup,command=self.toggle_startup).pack(side='left',padx=(8,10),pady=6)
        ttk.Checkbutton(box,text='…open hidden in the tray',variable=self.opt_start_tray,command=self.save_options).pack(side='left',padx=(0,10))
        ttk.Checkbutton(box,text='Start the bot automatically in',variable=self.opt_autobot,command=self.toggle_autobot).pack(side='left')
        mode=ttk.Combobox(box,textvariable=self.opt_automode,values=['Analysis only','DEMO execution','REAL execution'],state='readonly',width=15);mode.pack(side='left',padx=(4,14))
        mode.bind('<<ComboboxSelected>>',lambda _:self.toggle_autobot())
        ttk.Checkbutton(box,text='Minimize to system tray',variable=self.opt_tray,command=self.save_options).pack(side='left')
        self.opt_restart=tk.BooleanVar(value=self.settings['auto_restart'])
        ttk.Checkbutton(box,text='Auto-restart if the bot crashes',variable=self.opt_restart,command=self.save_options).pack(side='left',padx=(10,0))
        self.real_auto_label=tk.StringVar();self.refresh_real_auto_label()
        ttk.Label(box,textvariable=self.real_auto_label,foreground='#4a5d73').pack(side='right',padx=8)
    def refresh_real_auto_label(self):
        self.real_auto_label.set('REAL auto: ON' if self.settings.get('allow_real_auto') else 'REAL auto: off')
    def automode_value(self):
        v=self.opt_automode.get();return 'real' if v.startswith('REAL') else 'demo' if v.startswith('DEMO') else 'analysis'
    def save_options(self):
        mode=self.automode_value()
        if mode!='real' or not self.opt_autobot.get():self.settings['allow_real_auto']=False  # leaving REAL or unticking withdraws it
        self.settings.update(minimize_to_tray=self.opt_tray.get(),start_in_tray=self.opt_start_tray.get(),autostart_bot=self.opt_autobot.get(),autostart_mode=mode,
                             auto_restart=self.opt_restart.get())
        try:save_settings(self.settings)
        except Exception as e:self.write('Launcher settings not saved: '+str(e))
        self.refresh_real_auto_label()
    def toggle_startup(self):
        try:
            path=set_startup(self.opt_startup.get())
            self.write(('Start with Windows ON: ' if self.opt_startup.get() else 'Start with Windows OFF: removed ')+str(path))
            if self.opt_startup.get() and not self.opt_autobot.get():
                if messagebox.askyesno('Start the bot too?','Windows will now open the launcher at start-up.\n\n'
                    'Should it also start the bot automatically in '+self.opt_automode.get()+'?\n'
                    '(REAL execution needs a separate confirmation.)'):
                    self.opt_autobot.set(True);self.toggle_autobot()
                else:self.write('Only the launcher will open at Windows start; press Start bot yourself, or tick "Start the bot automatically in".')
        except Exception as e:self.opt_startup.set(startup_enabled());messagebox.showerror('Start with Windows','Could not change the Startup entry: '+str(e))
    def toggle_autobot(self):
        if self.opt_autobot.get() and self.automode_value()=='real' and not self.settings.get('allow_real_auto'):
            login,server=read_real_account(ROOT/'config.json')
            if not (login and server):
                messagebox.showerror('REAL auto-start','Save the REAL execution account first.');self.opt_automode.set('Analysis only')
            elif messagebox.askyesno('Allow REAL execution to start automatically?',
                    f'Whenever the launcher opens, including after a Windows restart, the bot will start REAL execution '
                    f'on account {login} at {server} WITHOUT asking you, and can send REAL-MONEY orders.\n\n'
                    'Every automatic start still runs the REAL account check (same account and server, hedging, permissions) '
                    'and logs the risk settings. It also allows automatic restarts after a crash if that option is on.\n\n'
                    'You can switch this off any time by choosing another mode or unticking the box.\n\nAllow REAL auto-start?',
                    icon='warning',default='no'):
                self.settings['allow_real_auto']=True;self.write(f'REAL auto-start allowed for {login} @ {server}.')
            else:self.opt_automode.set('Analysis only')
        if self.opt_autobot.get() and self.opt_automode.get().startswith('DEMO') and not (self.settings['autostart_bot'] and self.settings['autostart_mode']=='demo'):
            if not messagebox.askyesno('Auto-start in DEMO execution','Whenever the launcher opens (including at Windows start) the bot will start in DEMO execution without asking, and may send DEMO orders. Continue?',default='no'):
                self.opt_automode.set('Analysis only')
        self.save_options()
    def startup_notice(self):
        self.write('Launcher opened by Windows start-up.')
        if self.settings['autostart_bot']:
            self.write(f"The bot will start automatically in {self.settings['autostart_mode']} mode in about 20 s (waiting for MT5 to log in).")
        else:
            msg='The bot was not started: "Start the bot automatically in" is off. Tick it, or press Start bot.'
            self.write(msg)
            if self.tray:self.tray.balloon('Bot not started',msg)
    def auto_start(self):
        if self.bot and self.bot.poll() is None:return
        if self.busy or self.update_busy:self.root.after(10000,self.auto_start);return
        self.auto_tries+=1;mode=self.settings['autostart_mode']
        self.write(f'Auto-start attempt {self.auto_tries}/10 in {mode} mode…')
        self.mode.set(mode);self.start(auto=True);self.root.after(90000,self.auto_check)
    def auto_check(self):
        if self.bot and self.bot.poll() is None:return
        if self.auto_tries<10:self.write('Bot not running yet (MT5 may still be starting). Retrying in 60 s.');self.root.after(60000,self.auto_start)
        else:
            self.write('Auto-start gave up after 10 attempts. Open MT5, log in, then press Start bot.')
            if self.tray:self.tray.balloon('Bot not started','Auto-start gave up. Open MT5, log in and press Start bot.')
    # ---------------------------------------------------------------- auto-restart after an unexpected exit
    RESTART_DELAYS=(15,30,60,120,300)  # seconds; at most 5 restarts per 30 minutes
    def handle_crash(self,code):
        mode=(self.last_run or ('analysis',None))[0]
        self.write(f'Bot exited unexpectedly (code {code}). Broker SL/TP stay at the broker; local management paused.')
        if mode=='real' and not self.settings.get('allow_real_auto'):
            msg='REAL bot stopped unexpectedly. It is NOT restarted automatically: open the launcher and press Start bot.'
            self.write(msg)
            if self.tray:self.tray.balloon('REAL bot stopped',msg)
            return
        if not self.settings.get('auto_restart'):
            if self.tray:self.tray.balloon('Bot stopped','The bot exited unexpectedly. Auto-restart is off.')
            return
        now=time.time();self.restart_times=[t for t in self.restart_times if now-t<1800]
        if len(self.restart_times)>=len(self.RESTART_DELAYS):
            msg='Bot crashed 5 times in 30 minutes; auto-restart paused. Check the log, MT5 and the internet connection, then press Start bot.'
            self.write(msg)
            if self.tray:self.tray.balloon('Auto-restart paused',msg)
            return
        delay=self.RESTART_DELAYS[len(self.restart_times)];self.restart_times.append(now)
        self.write(f'Auto-restart {len(self.restart_times)}/5 in {delay} s ({mode} mode)…')
        self.status.set(f'Bot crashed · restarting in {delay} s ({mode})')
        if self.tray:self.tray.balloon('Bot crashed',f'Restarting in {delay} s ({mode} mode).')
        self.root.after(delay*1000,self.do_restart)
    def do_restart(self):
        if self.closing or self.stop_requested or (self.bot and self.bot.poll() is None):return
        if self.busy or self.update_busy:self.root.after(10000,self.do_restart);return
        mode,port=self.last_run
        self.mode.set(mode);self.port.set(str(port));self.restarting=True
        self.start(auto=True)
        self.root.after(60000,self.restart_check)
    def restart_check(self):
        if self.bot and self.bot.poll() is None or self.stop_requested or self.closing:return
        self.restarting=False;self.write('Auto-restart attempt did not start the bot (see messages above).');self.handle_crash('start failed')
    # ---------------------------------------------------------------- self-update from GitHub (app_updater.py)
    def build_updates(self,root):
        box=ttk.LabelFrame(root,text='Updates from GitHub (each update is tested in a separate copy first · never installed automatically during REAL execution)')
        box.pack(fill='x',padx=18,pady=(2,4))
        self.version_text=tk.StringVar(value=self.version_label());ttk.Label(box,textvariable=self.version_text).pack(side='left',padx=(8,10),pady=6)
        self.update_check_button=ttk.Button(box,text='Check now',command=lambda:self.check_updates(manual=True));self.update_check_button.pack(side='left',padx=3)
        self.update_install_button=ttk.Button(box,text='Install update',command=lambda:self.install_update(manual=True),state='disabled')
        self.update_install_button.pack(side='left',padx=3)
        self.opt_upd_check=tk.BooleanVar(value=self.settings['update_auto_check']);self.opt_upd_install=tk.BooleanVar(value=self.settings['update_auto_install'])
        ttk.Checkbutton(box,text='Check automatically',variable=self.opt_upd_check,command=self.save_update_options).pack(side='left',padx=(12,4))
        ttk.Checkbutton(box,text='Install automatically',variable=self.opt_upd_install,command=self.save_update_options).pack(side='left',padx=4)
        self.update_state=tk.StringVar(value='Not checked yet');ttk.Label(box,textvariable=self.update_state,foreground='#4a5d73').pack(side='left',padx=10)
    def version_label(self):
        v=app_updater.current_version(ROOT)
        return f"Installed: {v['hash']} · {v['date']}" if v else 'Installed: unknown (not a git checkout)'
    def save_update_options(self):
        was_on=self.settings['update_auto_check']
        self.settings.update(update_auto_check=self.opt_upd_check.get(),update_auto_install=self.opt_upd_install.get())
        try:save_settings(self.settings)
        except Exception as e:self.write('Launcher settings not saved: '+str(e))
        if self.settings['update_auto_check'] and not was_on:self.root.after(2000,self.update_tick)
    def update_tick(self,gen=None):
        """Periodic check. Each (re)start opens a new generation, so toggling the option never stacks timers."""
        if gen is None:self.update_gen+=1;gen=self.update_gen
        if gen!=self.update_gen or self.closing or not self.settings['update_auto_check']:return
        self.check_updates(manual=False)
        self.root.after(self.settings['update_check_minutes']*60000,lambda:self.update_tick(gen))
    def bot_running(self):return bool(self.bot and self.bot.poll() is None)
    def update_buttons(self,enabled):
        self.update_check_button.configure(state='normal' if enabled else 'disabled')
        avail=enabled and bool(self.update_info) and self.update_info.get('state')==app_updater.AVAILABLE
        self.update_install_button.configure(state='normal' if avail else 'disabled')
    def check_updates(self,manual=False):
        if self.update_busy:
            if manual:messagebox.showinfo('Updates','An update check or install is already running.')
            return
        self.update_busy=True;self.update_buttons(False);self.update_state.set('Checking GitHub for updates…')
        def work():
            try:info=app_updater.status(ROOT,self.settings['update_branch'])
            except Exception as exc:info=dict(state=app_updater.ERROR,message='Update check failed: '+str(exc)[:200],commits=[],remote=None)
            self.events.put(('update_status',(info,manual)))
        threading.Thread(target=work,daemon=True).start()
    def on_update_status(self,info,manual):
        self.update_busy=False;self.update_info=info;self.update_buttons(True);self.update_state.set(info['message'])
        if info['state']!=app_updater.AVAILABLE:
            if manual:(messagebox.showinfo if info['state'] in (app_updater.UP_TO_DATE,app_updater.AHEAD) else messagebox.showwarning)('Updates',info['message'])
            return
        commits='\n'.join(info['commits'][:8])+('\n…' if len(info['commits'])>8 else '')
        self.write('Update available: '+info['message']+'\n'+commits)
        if manual:
            if messagebox.askyesno('Install update?',info['message']+'\n\n'+commits+'\n\nThe update is tested in a separate copy first; '
                'if any test fails nothing is changed. If it installs, the bot restarts on the new version. Install now?'):
                self.install_update(manual=True)
            return
        if info.get('remote') and info['remote']==self.failed_target:  # same version already failed: wait for a newer one
            self.update_state.set(f"Update {info['remote']} failed its tests earlier · waiting for a newer version (Install update to retry)")
            return
        if self.settings['update_auto_install']:self.install_update(manual=False)
        elif self.tray:self.tray.balloon('Update available',info['message']+' Open the launcher to install it.')
    def install_update(self,manual=False):
        if self.update_busy:
            if manual:messagebox.showinfo('Updates','An update check or install is already running.')
            return
        if self.busy:  # setup / requirement checks / a bot start are running
            if manual:messagebox.showinfo('Updates','Wait for the current setup/check/start to finish, then install.');return
            self.write('Update deferred: setup/check in progress; retrying in 2 minutes.');self.root.after(120000,lambda:self.install_update(False));return
        mode=(self.last_run or ('analysis',None))[0]
        if self.bot_running() and mode=='real':
            if not manual:
                msg='Update available, but the bot is in REAL execution: updates are never installed automatically then. Use Install update when ready.'
                self.write(msg);self.update_state.set('Update available · install it manually (REAL execution is running)')
                if self.tray:self.tray.balloon('Update available',msg)
                return
            if not messagebox.askyesno('Install during REAL execution?','The REAL bot will be stopped gracefully, updated, then started again '
                    '(you will be asked to confirm the REAL start unless REAL auto-start is allowed). Broker SL/TP stay at the broker meanwhile. Continue?',
                    icon='warning',default='no'):return
        self.update_busy=True;self.update_buttons(False);self.update_state.set('Installing update · testing it in a separate copy (about 2 minutes)…')
        if self.tray:self.tray.set_tooltip(self.tip('installing update…'))
        def work():
            try:res=app_updater.apply(ROOT,self.python(),self.settings['update_branch'],log=self.write)
            except Exception as exc:res=dict(ok=False,state=app_updater.ERROR,message='Update failed: '+str(exc)[:300],launcher_changed=False)
            self.events.put(('update_result',res))
        threading.Thread(target=work,daemon=True).start()
    def on_update_result(self,res):
        self.update_busy=False;self.update_info=None;self.update_buttons(True)
        self.version_text.set(self.version_label());self.update_state.set(res['message']);self.write(res['message'])
        if self.tray:self.tray.set_tooltip(self.tip('bot running' if self.bot_running() else 'ready'))
        if not res['ok']:
            if res['state'] in (app_updater.TESTS_FAILED,app_updater.DEPS_FAILED):self.failed_target=res.get('to')
            if self.tray:self.tray.balloon('Update not installed',res['message'])
            return
        if res['state']!=app_updater.UPDATED:return  # nothing was pending after all
        self.failed_target=None
        if self.tray:self.tray.balloon('Update installed',res['message'])
        if res.get('launcher_changed'):
            msg='The launcher itself was updated: close and reopen it (Exit launcher, then START_APP.cmd) to use the new launcher.'
            self.write(msg)
            if self.tray:self.root.after(8000,lambda:self.tray.balloon('Reopen the launcher',msg))
        if self.bot_running():
            self.write('Restarting the bot on the new version…');self.update_restart_pending=True;self.stop()
    # ---------------------------------------------------------------- tray
    def start_tray(self):
        try:
            if not ICON_FILE.exists():write_icon(ICON_FILE)
            self.tray=TrayIcon(self.tip('ready'),ICON_FILE,lambda name:self.events.put(('tray',name)))
            if not self.tray.start():self.tray=None
        except Exception:self.tray=None
    def on_unmap(self,event):
        if event.widget is self.root and self.settings['minimize_to_tray'] and self.tray and self.root.state()=='iconic':self.root.after(50,self.to_tray)
    def to_tray(self):
        if not self.tray:return
        self.root.withdraw();show_console(False)
        if not self.tray_hint_shown:
            self.tray_hint_shown=True;self.tray.balloon('Still running','Grympha Trading Desk is in the tray. Click the icon to open it.')
    def show_window(self):
        show_console(True);self.root.deiconify();self.root.state('normal');self.root.lift();self.root.focus_force()
    def quit_app(self):
        if self.tray:self.tray.stop()
        show_console(True);self.root.destroy()
    def acquire_lock(self):
        import msvcrt
        self.lock=open(ROOT/'launcher.lock','a+b');self.lock.seek(0);self.lock.write(b'0');self.lock.flush();self.lock.seek(0)
        try:msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('Launcher already running in this folder. Use its existing window.')
    def python(self):
        v=VENV/'Scripts'/'python.exe'
        if v.is_file():
            try:
                r=subprocess.run([str(v),'-c','import sys,struct;assert sys.version_info[:2]==(3,11) and struct.calcsize("P")==8'],capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode==0:return str(v)
            except Exception:pass
        return sys.executable
    def write(self,text):self.events.put(('log',str(text)))
    def task(self,label,fn):
        if self.update_busy:  # the update rewrites program files; nothing else may run meanwhile
            messagebox.showinfo('Updating','An update is being checked or installed. Try again in a minute.');return
        if self.busy or self.bot and self.bot.poll() is None:
            messagebox.showinfo('Busy','Stop the bot or wait for the current check/setup first.');return
        self.busy=True;self.status.set(label)
        for b in self.buttons:b.configure(state='disabled')
        def work():
            try:fn()
            except Exception as exc:self.write('ERROR: '+str(exc))
            finally:self.events.put(('done',label))
        threading.Thread(target=work,daemon=True).start()
    def command(self,args,timeout=600):
        self.write('Running: '+Path(args[0]).name+' '+ ' '.join(Path(a).name if os.path.isabs(a) else a for a in args[1:]))
        p=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,env={**os.environ,'PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'})
        def consume():
            for line in p.stdout:self.write(line.rstrip())
        reader=threading.Thread(target=consume,daemon=True);reader.start()
        try:code=p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.terminate();p.wait();raise RuntimeError('Helper timed out. Check log and connection; no automatic retry.')
        reader.join(timeout=2)
        if code:raise RuntimeError('Helper failed (exit '+str(code)+'). Review the messages above.')
    def collect(self):
        detected=discover_terminals()
        if detected:self.write('Detected MT5 installations: '+'; '.join(detected))
        else:self.write('No MT5 installation found in common locations; portable installs can be selected manually.')
        p=subprocess.run([self.python(),str(ROOT/'launcher_checks.py')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
        if p.returncode:raise RuntimeError('Checks failed to start. Install requirements; verify config.json. '+p.stderr[-300:])
        report=json.loads(p.stdout);self.events.put(('report',report));return report
    def check(self):self.task('Checking requirements / MT5…',self.collect)
    def setup(self):
        def work():
            for name in ('config.json','.env'):
                target=ROOT/name;source=ROOT/('config.example.json' if name=='config.json' else '.env.example')
                if not target.exists():shutil.copy2(source,target);self.write('Created '+name+' from example. Existing files are preserved.')
            v=VENV/'Scripts'/'python.exe'
            if VENV.exists() and self.python()!=str(v):
                dest=ROOT/('.venv.backup.'+datetime.now().strftime('%Y%m%d_%H%M%S'));VENV.rename(dest);self.write('Moved unusable/copied virtual environment to '+dest.name)
            if not v.is_file():self.command([sys.executable,'-m','venv',str(VENV)])
            self.command([str(v),'-m','pip','install','-r',str(ROOT/'requirements.txt')],timeout=900)
            self.write('Setup finished. No credentials changed; no demo orders enabled. Select the correct MT5 terminal next.');self.collect()
        self.task('Installing local environment and packages from PyPI…',work)
    def cfg(self):return json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    def choose_terminal(self):
        if self.busy or self.bot and self.bot.poll() is None:return
        found=discover_terminals()
        path=filedialog.askopenfilename(title='Select intended MT5 terminal64.exe',initialdir=str(Path(found[0]).parent) if found else os.environ.get('ProgramFiles','C:/'),filetypes=[('MT5 terminal','terminal64.exe')])
        if not path:return
        if Path(path).name.lower()!='terminal64.exe':messagebox.showerror('Invalid terminal','Choose terminal64.exe from your intended MT5 installation.');return
        try:
            cfg=self.cfg();cfg['terminal_path']=path;p=ROOT/'config.json';backup(p);temp=p.with_suffix('.launcher.tmp');temp.write_text(json.dumps(cfg,indent=2),encoding='utf-8');os.replace(temp,p)
            self.help.set('MT5 terminal saved: '+path);self.check()
        except Exception:messagebox.showerror('Setup needed','Run Setup first, or repair config.json. No terminal selection saved.')
    def open_terminal(self):
        try:
            path=self.cfg().get('terminal_path')
            if path and Path(path).is_file():os.startfile(path)
            else:
                found=discover_terminals()
                self.help.set('Detected: '+('; '.join(found) or 'No terminal found. Install RoboForex MT5 first.')+' — use Choose MT5 terminal to select it.')
                if not found:webbrowser.open('https://www.metatrader5.com/en/download')
        except Exception:self.help.set('Click Setup, then Choose MT5 terminal. Log into the account yourself in MT5.')
    def install_ea(self):
        def work():
            report=self.collect();data=report.get('terminal_data_path')
            if not data:raise RuntimeError('MT5 data folder unavailable. Choose/open terminal and connect first.')
            dest=Path(data)/'MQL5'/'Experts'/'BBMA_MT5_Calendar_Bridge.mq5';dest.parent.mkdir(parents=True,exist_ok=True);backup(dest);shutil.copy2(ROOT/dest.name,dest)
            self.write('EA copied to '+str(dest))
            self.write('NEXT: In MT5 press F4, open this .mq5, press F7 to compile, then attach ONE bridge to one chart. Inputs: USD,GBP,JPY,EUR,AUD,CAD,CHF,NZD. Keep chart open. No automatic compile/attachment claimed.')
            self.events.put(('ea_folder',str(dest.parent)))
        self.task('Installing read-only Calendar Bridge source…',work)
    def script(self,name):
        args=[self.python(),str(ROOT/'bbma_bot.py'),name] if name.startswith('--') else [self.python(),str(ROOT/name)]
        def work():
            self.command(args,timeout=180)
            if name=='configure_clock_offset.py':self.collect()   # show the quote rows with the new offset
        self.task('Running '+name,work)
    def port_number(self):
        value=int(self.port.get())
        if not 1024<=value<=65535:raise ValueError('Port must be 1024..65535')
        return value
    def start(self,auto=False):
        try:port=self.port_number()
        except Exception as e:messagebox.showerror('Invalid port',str(e));return
        mode=self.mode.get()
        if auto and mode=='real' and not self.settings.get('allow_real_auto'):
            self.write('REAL auto-start is not allowed; press Start bot.');return
        if port!=self.settings.get('port'):
            self.settings['port']=port
            try:save_settings(self.settings)
            except Exception:pass
        if mode=='real':
            try:
                checked=subprocess.run([self.python(),str(ROOT/'bbma_bot.py'),'--real-check'],cwd=ROOT,
                                       capture_output=True,text=True,encoding='utf-8',timeout=40,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                if checked.returncode:
                    raise RuntimeError(checked.stderr.strip() or checked.stdout.strip())
                info=json.loads(checked.stdout)
            except Exception as exc:
                if auto:self.write('REAL account check failed (will retry): '+str(exc)[:300]);return
                messagebox.showerror('REAL account check failed',str(exc));return
            sizing=f"{info['risk_percent']}% equity per trade" if info['risk_enabled'] else 'Fixed-lot fallback 0.01'
            portfolio=info['portfolio']
            limits=(f"{portfolio['total_percent']}% total / {portfolio['pair_percent']}% per pair / {portfolio['currency_percent']}% per currency"
                    if portfolio['enabled'] else 'Portfolio guard monitor only')
            overrides=', '.join(f'{pair}: {lot}' for pair,lot in info['pair_lots'].items()) or 'None'
            if auto:
                self.write(f"REAL auto-start (allowed in settings): account {info['login']} @ {info['server']} · sizing {sizing} · "
                           f"fixed lots {overrides} · cap {info['position_cap']} · portfolio {limits}")
            elif not messagebox.askyesno('Start REAL execution',f"Real money orders on account {info['login']} at {info['server']}.\n"
                f"Sizing: {sizing}\nFixed-lot overrides: {overrides}\n"
                f"Position/order cap: {info['position_cap']}\nPortfolio: {limits}\n\nStart REAL execution?",default='no'):
                return
        if mode=='demo' and not auto and not messagebox.askyesno('Start DEMO execution','This mode sends new DEMO orders and applies enabled management to bot-owned positions. Lot sizing follows saved risk controls. Proceed on the intended DEMO account?'):return
        def work():
            report=self.collect();rows=report['rows']
            if mode in ('demo','real'):
                bad=[r for r in rows if r['status']=='FAIL']
                acct=next((r for r in rows if r['name']=='Account type'),{})
                if mode=='demo' and acct.get('account_mode')!='DEMO':
                    raise RuntimeError('DEMO execution needs MT5 logged into a DEMO account (currently '+str(acct.get('account_mode','unknown'))+'). Use Analysis only, or switch account in MT5.')
                if mode=='real' and not acct.get('matches_real'):
                    raise RuntimeError('REAL execution needs MT5 logged into the saved REAL account. Check the REAL execution account row.')
            else:bad=[r for r in rows if r['status']=='FAIL' and r['name'] in ['Python','Configuration','Folder permissions','Telegram','MetaTrader5','numpy','pandas','python-dotenv','ta']]
            if bad:raise RuntimeError('Fix requirements first: '+', '.join(r['name'] for r in bad))
            with socket.socket() as sock:
                try:sock.bind(('127.0.0.1',port))
                except OSError:raise RuntimeError('Dashboard port in use. Stop older bot/server or choose another port. No bot started.')
            if mode=='demo':self.command([self.python(),str(ROOT/'bbma_bot.py'),'--demo-check'],timeout=40)
            args=[self.python(),'-u',str(ROOT/'bbma_bot.py'),'--dashboard','--dashboard-port',str(port)]
            if mode in ('demo','real'):args.append('--'+mode+'-execution')
            self.bot=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,env={**os.environ,'PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'})
            time.sleep(.8)
            if self.bot.poll() is not None:
                output=self.bot.stdout.read();self.write(output);raise RuntimeError('Bot exited during startup; see log.')
            self.running_port=port;self.stop_requested=False;self.last_run=(mode,port)
            self.events.put(('remember_mode',mode))
            self.events.put(('started',(mode,port)))
            def consume():
                for line in self.bot.stdout:self.write(line.rstrip())
                self.events.put(('exited',self.bot.wait()))
            threading.Thread(target=consume,daemon=True).start()
        self.task('Checking readiness before start…',work)
    def stop(self):
        self.stop_requested=True
        if self.bot and self.bot.poll() is None:
            try:self.bot.send_signal(signal.CTRL_BREAK_EVENT);self.status.set('Stopping bot gracefully… existing broker positions remain open.')
            except Exception as e:messagebox.showerror('Stop unavailable','Could not signal the bot. Keep this window open and use its console to stop it. '+str(e))
    def restart_bot(self):
        if self.busy:
            messagebox.showinfo('Working','Wait for setup/checks to finish before restarting.');return
        if self.bot and self.bot.poll() is None:
            self.restart_pending=True;self.restart_button.configure(state='disabled')
            self.write('Restarting bot…');self.stop()
        else:
            self.start()
    def open_dashboard(self):
        try:webbrowser.open('http://127.0.0.1:'+str(self.running_port if self.bot and self.bot.poll() is None else self.port_number())+'/home')
        except Exception:messagebox.showerror('Port','Enter a valid dashboard port first.')
    def show_row(self,*_):
        selected=self.table.selection()
        if selected:
            index=int(selected[0]);r=self.report['rows'][index];self.help.set(r['name']+': '+r['detail']+' '+r.get('guide',''))
    def guide(self):
        win=tk.Toplevel(self.root);win.title('Setup and recovery guide');win.geometry('850x650')
        text=tk.Text(win,wrap='word',padx=14,pady=14);text.pack(fill='both',expand=True)
        text.insert('end',(ROOT/'LAUNCHER_GUIDE.md').read_text(encoding='utf-8'));text.configure(state='disabled')
    def drain(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='log':
                    self.log.insert('end',str(data)+'\n');self.log.see('end')
                    if 'ANOTHER BOT is trading this account' in str(data) and self.tray:self.tray.balloon('Another bot on this account','New entries are paused. Stop the other bot, then acknowledge in the dashboard (System Health).')
                elif kind=='report':
                    self.report=data;self.table.delete(*self.table.get_children())
                    for i,r in enumerate(data['rows']):self.table.insert('', 'end',iid=str(i),values=(r['name'],r['status'],r['detail']),tags=(r['status'],))
                elif kind=='ea_folder':os.startfile(data)
                elif kind=='remember_mode':
                    if self.settings.get('last_mode')!=data:
                        self.settings['last_mode']=data
                        try:save_settings(self.settings)
                        except Exception:pass
                elif kind=='tray':
                    if data=='show':self.show_window()
                    elif data=='dashboard':self.open_dashboard()
                    elif data=='stop':self.show_window();self.stop()
                    elif data=='restart':self.show_window();self.restart_bot()
                    elif data=='update':self.show_window();self.check_updates(manual=True)
                    elif data=='exit':self.show_window();self.close()
                elif kind=='update_status':self.on_update_status(*data)
                elif kind=='update_result':self.on_update_result(data)
                elif kind=='telegram':
                    self.tg_busy(False)
                    import telegram_config as tg
                    if 'error' in data:self.tg_state.set('Telegram: '+data['error'])
                    elif 'sent' in data:self.tg_state.set('Test message sent ✓ · check Telegram, then press Save')
                    elif 'chats' in data:
                        chats=data['chats']
                        if not chats:self.tg_state.set('No chats found · open your bot in Telegram, press Start / send any message, then try again')
                        else:
                            self.tg_chat.set(chats[0]['id'])
                            self.tg_state.set('Chat ID found: '+chats[0]['id']+(' ('+chats[0]['name']+')' if chats[0]['name'] else '')+(f' · {len(chats)} chats seen, newest used' if len(chats)>1 else '')+' · press Send test')
                elif kind=='mt5_account':
                    self.fill_button.configure(state='normal')
                    if 'error' in data:self.real_state.set(data['error'])
                    else:
                        self.real_login.set(str(data['login']));self.real_server.set(data['server'])
                        kind_txt={0:'DEMO',1:'CONTEST',2:'REAL'}.get(data.get('trade_mode'),'UNKNOWN')
                        self.real_state.set(f'Read from MT5: {kind_txt} account · press Save REAL account to use it'+(' (warning: not a REAL account)' if kind_txt!='REAL' else ''))
                elif kind=='started':
                    self.status.set('Running '+data[0]+' · dashboard port '+str(data[1]));self.stop_button.configure(state='normal');self.restart_button.configure(state='normal')
                    if self.tray:self.tray.set_tooltip(self.tip(f'bot running ({data[0]}) · port {data[1]}'))
                    if self.restarting:self.restarting=False;self.write('Auto-restart: bot is running again ('+data[0]+').')
                    elif not (STARTED_BY_WINDOWS and self.root.state()=='withdrawn'):webbrowser.open('http://127.0.0.1:'+str(data[1])+'/home')
                    elif self.tray:self.tray.balloon('Bot started',f'Running in {data[0]} mode. Dashboard: http://127.0.0.1:{data[1]}')
                elif kind=='exited':
                    self.status.set('Bot stopped · exit '+str(data)+' · broker positions were not automatically closed');self.stop_button.configure(state='disabled');self.restart_button.configure(state='disabled')
                    if self.tray:
                        self.tray.set_tooltip(self.tip('bot stopped'))
                        if self.root.state()=='withdrawn' and not self.closing:self.tray.balloon('Bot stopped','The bot exited (code '+str(data)+'). Open the launcher for details.')
                    for b in self.buttons:b.configure(state='normal')
                    if self.closing:self.quit_app();return
                    if self.update_restart_pending:  # stopped to apply an update: start it again on the new code
                        self.update_restart_pending=False;self.stop_requested=False
                        mode,port=self.last_run or (self.mode.get(),self.port_number())
                        self.mode.set(mode);self.port.set(str(port))
                        self.write(f'Bot stopped for the update — starting it again in {mode} mode…')
                        if mode=='real' and not self.settings.get('allow_real_auto'):self.root.after(1500,self.start)  # asks to confirm REAL
                        else:self.root.after(1500,self.do_restart)  # auto start, with the crash-retry safety net
                    elif self.restart_pending:
                        self.restart_pending=False
                        mode,port=self.last_run or (self.mode.get(),self.port_number())
                        self.mode.set(mode);self.port.set(str(port));self.stop_requested=False
                        self.write('Bot stopped — restarting in '+mode+' mode…')
                        self.root.after(1500,self.start)
                    elif not self.stop_requested:self.handle_crash(data)
                elif kind=='done':
                    self.busy=False
                    if not self.bot or self.bot.poll() is not None:
                        for b in self.buttons:b.configure(state='normal')
                        self.status.set('Ready · review results/log for '+data)
        except queue.Empty:pass
        self.root.after(100,self.drain)
    def close(self):
        if self.update_busy:messagebox.showinfo('Updating','Wait for the update check/install to finish before closing.');return
        if self.busy:messagebox.showinfo('Working','Wait for setup/checks to finish before closing.');return
        if self.bot and self.bot.poll() is None:
            if messagebox.askyesno('Stop bot and close?','Stop the bot before closing? Existing demo positions and broker SL/TP remain; local management stops.'):
                self.closing=True;self.stop()
            return
        self.quit_app()

if __name__=='__main__':
    root=tk.Tk()
    try:
        if os.name!='nt':raise RuntimeError('This launcher requires Windows. No setup or trading action performed.')
        app=App(root);root.mainloop()
    except Exception as exc:messagebox.showerror('Automated Trading Agent',str(exc));root.destroy();sys.exit(1)
