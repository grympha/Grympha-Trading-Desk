// BBMA MT5 Calendar Bridge 1.01 - READ ONLY, no trading or DLL functions.
// Install: File > Open Data Folder > MQL5 > Experts; compile in MetaEditor.
// Attach ONE instance to one chart. Output: MQL5/Files/bbma_mt5_calendar.json.
// Exports schema bbma.mt5.calendar.v1, consumed by BBMA Python v1.8.
// Run configure_v1_8.py and keep this EA attached to one chart.
#property strict
#property version "1.01"
#property description "Read-only MT5 economic calendar exporter. Does not trade."

input string InpCurrencies="USD,GBP,JPY,EUR,AUD,CAD,CHF,NZD";
input int InpHeartbeatSeconds=30;
input int InpCalendarRefreshSeconds=300;
input int InpCacheMaxAgeSeconds=900;
input int InpDaysAhead=5;
input bool InpUseVerifiedManualOffset=false;
input int InpVerifiedServerUTCOffsetMinutes=120;

const string OUTPUT="bbma_mt5_calendar.json";
int g_lock=INVALID_HANDLE;
string g_currencies[],g_events="[]",g_error="Waiting for first complete calendar snapshot";
string g_status="STARTING";
datetime g_last_fetch=0,g_from=0,g_to=0,g_previous_quote=0;
ulong g_last_attempt_ms=0,g_last_success_ms=0;
long g_offset=0;
bool g_have_attempt=false,g_have_cache=false,g_clock_ok=false,g_quote_advanced=false;
MqlCalendarEvent g_event_cache[];
MqlCalendarCountry g_country_cache[];

string J(const string s)
{
   string r="\"";
   for(int i=0;i<StringLen(s);i++)
   {
      ushort c=StringGetCharacter(s,i);
      if(c==34)r+="\\\"";
      else if(c==92)r+="\\\\";
      else if(c<32)r+=StringFormat("\\u%04X",(uint)c);
      else r+=StringSubstr(s,i,1);
   }
   return r+"\"";
}
string N(const long v){return IntegerToString(v);}
string B(const bool v){return v?"true":"false";}

bool Selected(const string currency)
{
   for(int i=0;i<ArraySize(g_currencies);i++)
      if(currency==g_currencies[i])return true;
   return false;
}

bool EventByID(const ulong id,MqlCalendarEvent &out)
{
   for(int i=0;i<ArraySize(g_event_cache);i++)
      if(g_event_cache[i].id==id){out=g_event_cache[i];return true;}
   ResetLastError();
   if(!CalendarEventById(id,out))return false;
   int n=ArraySize(g_event_cache);
   if(ArrayResize(g_event_cache,n+1)!=n+1)return false;
   g_event_cache[n]=out;
   return true;
}
bool CountryByID(const ulong id,MqlCalendarCountry &out)
{
   for(int i=0;i<ArraySize(g_country_cache);i++)
      if(g_country_cache[i].id==id){out=g_country_cache[i];return true;}
   ResetLastError();
   if(!CalendarCountryById(id,out))return false;
   int n=ArraySize(g_country_cache);
   if(ArrayResize(g_country_cache,n+1)!=n+1)return false;
   g_country_cache[n]=out;
   return true;
}

bool CheckClock(const datetime utc,const datetime server,string &why)
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      {why="MT5 is disconnected";return false;}
   if(utc<=0 || server<=0){why="Clock unavailable";return false;}
   long observed=(long)server-(long)utc;
   if(MathAbs((double)observed)>14*3600)
      {why="Implausible server clock offset; verify Windows/server time";return false;}
   long candidate=(long)MathRound((double)observed/900.0)*900;
   if(InpUseVerifiedManualOffset)
      candidate=(long)InpVerifiedServerUTCOffsetMinutes*60;
   if(MathAbs((double)(observed-candidate))>10)
      {why="Server/UTC offset cannot be verified against terminal clock";return false;}
   if(!InpUseVerifiedManualOffset)
   {
      datetime quote=TimeCurrent();
      long quote_age=(long)server-(long)quote;
      if(g_previous_quote>0 && quote>g_previous_quote)g_quote_advanced=true;
      g_previous_quote=quote;
      if(quote_age < -5 || quote_age>120 || !g_quote_advanced)
         {why="Auto clock verification needs fresh advancing quotes; do not infer from a stationary old quote";return false;}
   }
   if(candidate!=g_offset){g_have_cache=false;g_have_attempt=false;}
   g_offset=candidate;
   return true;
}

bool FetchCalendar(const datetime server,const datetime utc)
{
   MqlDateTime day;
   TimeToStruct(server,day);day.hour=0;day.min=0;day.sec=0;
   datetime from=StructToTime(day)-86400;
   datetime to=StructToTime(day)+(InpDaysAhead+1)*86400;
   MqlCalendarValue values[];
   ResetLastError();
   int count=CalendarValueHistory(values,from,to);
   int err=GetLastError();
   if(count<0 || err!=0 || count>25000 || count!=ArraySize(values))
   {
      g_error="Calendar query incomplete/failed; error="+N(err)+", rows="+N(count);
      return false;
   }
   // A globally empty response cannot demonstrate that the calendar is loaded.
   // A complete nonempty response containing zero relevant HIGH events is valid.
   if(count==0){g_error="Calendar returned no records; readiness unverified";return false;}
   ArrayResize(g_event_cache,0);ArrayResize(g_country_cache,0);
   string events="[";
   int exported=0;
   for(int i=0;i<count;i++)
   {
      if(IsStopped()){g_error="Exporter stopped during calendar query";return false;}
      MqlCalendarEvent event;
      if(!EventByID(values[i].event_id,event))
         {g_error="Event metadata missing; snapshot incomplete";return false;}
      if(event.importance!=CALENDAR_IMPORTANCE_HIGH)continue;
      MqlCalendarCountry country;
      if(!CountryByID(event.country_id,country))
         {g_error="Country/currency metadata missing; snapshot incomplete";return false;}
      if(!Selected(country.currency))continue;
      if(values[i].time<=0){g_error="Event time missing; snapshot incomplete";return false;}
      if(exported++>0)events+=",";
      bool uncertain=(event.time_mode!=CALENDAR_TIMEMODE_DATETIME);
      events+="{\"value_id\":"+J(StringFormat("%I64u",values[i].id))+
         ",\"event_id\":"+J(StringFormat("%I64u",event.id))+
         ",\"title\":"+J(event.name)+",\"currency\":"+J(country.currency)+
         ",\"country\":"+J(country.name)+",\"importance\":3"+
         ",\"time_mode\":"+N((long)event.time_mode)+",\"estimated_time\":"+B(uncertain)+
         ",\"server_epoch\":"+N((long)values[i].time)+
         ",\"utc_epoch_projection\":"+N((long)values[i].time-g_offset)+"}";
   }
   events+="]";
   // Commit only after ALL records and metadata have been checked.
   g_events=events;g_from=from;g_to=to;g_last_fetch=utc;
   g_last_success_ms=GetTickCount64();g_have_cache=true;g_error="";
   return true;
}

bool WriteSnapshot(const datetime utc,const datetime server,const bool ready)
{
   string currencies="[";
   for(int i=0;i<ArraySize(g_currencies);i++)
      {if(i>0)currencies+=",";currencies+=J(g_currencies[i]);}
   currencies+="]";
   string text="{\"schema\":\"bbma.mt5.calendar.v1\",\"bridge_version\":\"1.01\""+
      ",\"provider\":\"MT5 Economic Calendar\",\"status\":"+J(g_status)+
      ",\"ready\":"+B(ready)+",\"error\":"+J(g_error)+
      ",\"connected\":"+B((bool)TerminalInfoInteger(TERMINAL_CONNECTED))+
      ",\"updated_epoch\":"+N((long)utc)+",\"server_now_epoch\":"+N((long)server)+
      ",\"fetched_epoch\":"+N((long)g_last_fetch)+
      ",\"server_utc_offset_seconds\":"+N(g_offset)+
      ",\"clock_verified\":"+B(g_clock_ok)+
      ",\"offset_mode\":"+J(InpUseVerifiedManualOffset?"USER_VERIFIED_MANUAL":"OBSERVED_ADVANCING_QUOTES")+
      ",\"coverage_from_server_epoch\":"+N((long)g_from)+
      ",\"coverage_to_server_epoch\":"+N((long)g_to)+
      ",\"heartbeat_seconds\":"+N(InpHeartbeatSeconds)+
      ",\"max_cache_age_seconds\":"+N(InpCacheMaxAgeSeconds)+
      ",\"broker\":"+J(AccountInfoString(ACCOUNT_COMPANY))+
      ",\"server\":"+J(AccountInfoString(ACCOUNT_SERVER))+
      ",\"terminal_data_path\":"+J(TerminalInfoString(TERMINAL_DATA_PATH))+
      ",\"currencies\":"+currencies+
      ",\"time_note\":\"Calendar epochs use trade-server wall time. UTC projections assume the currently verified offset; future DST changes may invalidate them. Consumers should compare raw event server epochs to fresh server_now_epoch and enforce ready/freshness/coverage.\""+
      ",\"events\":"+(ready?g_events:"[]")+"}";
   string temp=OUTPUT+".tmp."+N(ChartID());
   int f=FileOpen(temp,FILE_WRITE|FILE_BIN);
   if(f==INVALID_HANDLE){Print("Calendar export FileOpen failed: ",GetLastError());return false;}
   uchar bytes[];
   int size=StringToCharArray(text,bytes,0,WHOLE_ARRAY,CP_UTF8)-1;
   ResetLastError();
   uint written=FileWriteArray(f,bytes,0,size);
   FileFlush(f);int error=GetLastError();FileClose(f);
   if((int)written!=size || error!=0)
      {Print("Calendar export incomplete write: ",error);FileDelete(temp);return false;}
   ResetLastError();
   if(!FileMove(temp,0,OUTPUT,FILE_REWRITE))
      {Print("Calendar export replacement failed: ",GetLastError(),"; reader must reject stale snapshot");return false;}
   return true;
}

void Publish()
{
   datetime utc=TimeGMT(),server=TimeTradeServer();
   string why="";
   g_clock_ok=CheckClock(utc,server,why);
   bool ready=false;
   if(!g_clock_ok){g_status="UNAVAILABLE";g_error=why;}
   else
   {
      ulong now_ms=GetTickCount64();
      bool due=!g_have_attempt || now_ms-g_last_attempt_ms>=(ulong)InpCalendarRefreshSeconds*1000;
      if(due)
      {
         g_have_attempt=true;g_last_attempt_ms=now_ms;
         // Publish unavailability before a potentially slow platform calendar call.
         g_status="UPDATING";g_error="Calendar refresh in progress";
         WriteSnapshot(utc,server,false);
         if(!FetchCalendar(server,utc))g_have_cache=false;
      }
      string after_query_clock_error="";
      g_clock_ok=CheckClock(TimeGMT(),TimeTradeServer(),after_query_clock_error);
      if(!g_clock_ok){g_have_cache=false;g_error=after_query_clock_error;}
      ready=g_clock_ok && g_have_cache && GetTickCount64()-g_last_success_ms<=(ulong)InpCacheMaxAgeSeconds*1000;
      if(ready){g_status="HEALTHY";g_error="";}
      else {g_status="UNAVAILABLE";if(g_error=="")g_error="Calendar cache expired";}
   }
   // UTC is read again so heartbeat reflects completion, not query start.
   bool saved=WriteSnapshot(TimeGMT(),TimeTradeServer(),ready);
   Comment("BBMA Calendar Bridge (READ ONLY)\nStatus: ",saved?g_status:"EXPORT ERROR",
      "\n",g_error,"\nNo order functions.\nOutput: MQL5\\Files\\",OUTPUT);
}

int OnInit()
{
   if(MQLInfoInteger(MQL_TESTER))
      {Print("Calendar bridge requires a running terminal, not Strategy Tester.");return INIT_FAILED;}
   if(InpHeartbeatSeconds<5 || InpHeartbeatSeconds>60 ||
      InpCalendarRefreshSeconds<60 || InpCalendarRefreshSeconds>600 ||
      InpCacheMaxAgeSeconds<InpCalendarRefreshSeconds || InpCacheMaxAgeSeconds>1800 ||
      InpDaysAhead<1 || InpDaysAhead>14 ||
      MathAbs(InpVerifiedServerUTCOffsetMinutes)>840)
      {Print("Invalid timer, cache, horizon or clock input");return INIT_PARAMETERS_INCORRECT;}
   string list=InpCurrencies;StringReplace(list," ","");StringToUpper(list);
   if(StringSplit(list,',',g_currencies)<1)return INIT_PARAMETERS_INCORRECT;
   for(int i=0;i<ArraySize(g_currencies);i++)
   {
      if(StringLen(g_currencies[i])!=3)return INIT_PARAMETERS_INCORRECT;
      for(int c=0;c<3;c++)
      {
         ushort letter=StringGetCharacter(g_currencies[i],c);
         if(letter<65 || letter>90)return INIT_PARAMETERS_INCORRECT;
      }
   }
   // Hold a non-shared file handle to prevent two writers in the SAME terminal.
   g_lock=FileOpen("bbma_mt5_calendar.lock",FILE_READ|FILE_WRITE|FILE_BIN);
   if(g_lock==INVALID_HANDLE)
      {Print("Calendar bridge already running or lock file unavailable. Use one chart only.");return INIT_FAILED;}
   if(!EventSetTimer(InpHeartbeatSeconds))return INIT_FAILED;
   Print("Calendar bridge export: ",TerminalInfoString(TERMINAL_DATA_PATH),"\\MQL5\\Files\\",OUTPUT);
   Publish();return INIT_SUCCEEDED;
}
void OnTimer(){Publish();}
void OnDeinit(const int reason)
{
   EventKillTimer();g_status="STOPPED";g_error="Calendar bridge removed/stopped";
   // Do not overwrite another instance's output if THIS instance never owned lock.
   if(g_lock!=INVALID_HANDLE)
   {
      WriteSnapshot(TimeGMT(),TimeTradeServer(),false);
      FileClose(g_lock);g_lock=INVALID_HANDLE;
   }
   Comment("");
}
