static int sqliteDefaultBusyCallback(
  void *ptr,               /* Database connection */
  int count                /* Number of times table has been busy */
){
#if SQLITE_OS_WIN || !defined(HAVE_NANOSLEEP) || HAVE_NANOSLEEP
  /* This case is for systems that have support for sleeping for fractions of
  ** a second.  Examples:  All windows systems, unix systems with nanosleep() */
  static const u8 delays[] =
     { 1, 2, 5, 10, 15, 20, 25, 25,  25,  50,  50, 100 };
  static const u8 totals[] =
     { 0, 1, 3,  8, 18, 33, 53, 78, 103, 128, 178, 228 };
# define NDELAY ArraySize(delays)
  sqlite3 *db = (sqlite3 *)ptr;
  int tmout = db->busyTimeout;
  int delay, prior;

  assert( count>=0 );
  if( count < NDELAY ){
    delay = delays[count];
    prior = totals[count];
  }else{
    delay = delays[NDELAY-1];
    prior = totals[NDELAY-1] + delay*(count-(NDELAY-1));
  }
  if( prior + delay > tmout ){
    delay = tmout - prior;
    if( delay<=0 ) return 0;
  }
  sqlite3OsSleep(db->pVfs, delay*1000);
  return 1;
#else
  /* This case for unix systems that lack usleep() support.  Sleeping
  ** must be done in increments of whole seconds */
  sqlite3 *db = (sqlite3 *)ptr;
  int tmout = ((sqlite3 *)ptr)->busyTimeout;
  if( (count+1)*1000 > tmout ){
    return 0;
  }
  sqlite3OsSleep(db->pVfs, 1000000);
  return 1;
#endif
}
