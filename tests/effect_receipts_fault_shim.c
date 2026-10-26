/* Disposable Linux qualification fixture, NOT engine/runtime implementation.
 * Every successful operation calls libc. No fake fsync successes. Target paths
 * and log are supplied explicitly by the isolated harness. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>

static int (*real_open)(const char *, int, ...);
static int (*real_openat)(int,const char *,int,...);
static ssize_t (*real_write)(int, const void *, size_t);
static int (*real_close)(int), (*real_sync)(int), (*real_lock)(int,int);
static int (*real_rename)(const char *,const char *);
static unsigned char targets[65536];
static unsigned writes, opens, closes, locks;

static void init(void) {
    if (real_open) return;
    real_open=dlsym(RTLD_NEXT,"open"); real_write=dlsym(RTLD_NEXT,"write");
    real_openat=dlsym(RTLD_NEXT,"openat");
    real_close=dlsym(RTLD_NEXT,"close"); real_sync=dlsym(RTLD_NEXT,"fsync");
    real_lock=dlsym(RTLD_NEXT,"flock"); real_rename=dlsym(RTLD_NEXT,"rename");
}
static int target(const char *path) {
    const char *prefix=getenv("EFFECT_FAULT_TARGET");
    return prefix && !strncmp(path,prefix,strlen(prefix));
}
static int tracked(int fd) { return fd>=0 && fd<65536 && targets[fd]; }
static int mode(const char *name) {
    const char *value=getenv("EFFECT_FAULT_MODE");
    return value && !strcmp(value,name);
}
static void note(const char *op,int fd,long amount,long result,int error) {
    int saved=errno; const char *path=getenv("EFFECT_FAULT_LOG");
    if (path) {
        char line[200]; int n=snprintf(line,sizeof(line),"%s fd=%d amount=%ld result=%ld errno=%d\n",op,fd,amount,result,error);
        int log=real_open(path,O_WRONLY|O_CREAT|O_APPEND,0600);
        if (log>=0) { real_write(log,line,(size_t)n); real_close(log); }
    }
    errno=saved;
}
static int opening(const char *path,int flags,mode_t bits) {
    init(); int selected=target(path) && (flags&(O_WRONLY|O_RDWR));
    if (selected && (mode("open") || mode("open-eintr")) && opens++==0) {
        int error=mode("open-eintr")?EINTR:EACCES;
        note("open-injected",-1,flags,-1,error);errno=error;return -1;
    }
    int fd=real_open(path,flags,bits), error=fd<0?errno:0;
    if (selected) { if (fd>=0 && fd<65536) targets[fd]=1; note("open-real",fd,flags,fd,error); }
    return fd;
}
int open(const char *path,int flags,...) {
    mode_t bits=0; if(flags&O_CREAT) { va_list a;va_start(a,flags);bits=va_arg(a,int);va_end(a); }
    return opening(path,flags,bits);
}
int open64(const char *path,int flags,...) {
    mode_t bits=0; if(flags&O_CREAT) { va_list a;va_start(a,flags);bits=va_arg(a,int);va_end(a); }
    return opening(path,flags,bits);
}
static int opening_at(int directory,const char *path,int flags,mode_t bits) {
    init();int selected=target(path) && (flags&(O_WRONLY|O_RDWR));
    if(selected && (mode("open") || mode("open-eintr")) && opens++==0) {
        int error=mode("open-eintr")?EINTR:EACCES;
        note("openat-injected",-1,flags,-1,error);errno=error;return -1;
    }
    int fd=real_openat(directory,path,flags,bits),error=fd<0?errno:0;
    if(selected) {if(fd>=0 && fd<65536) targets[fd]=1;note("openat-real",fd,flags,fd,error);}
    return fd;
}
int openat(int directory,const char *path,int flags,...) {
    mode_t bits=0;if(flags&O_CREAT) {va_list a;va_start(a,flags);bits=va_arg(a,int);va_end(a);}
    return opening_at(directory,path,flags,bits);
}
int openat64(int directory,const char *path,int flags,...) {
    mode_t bits=0;if(flags&O_CREAT) {va_list a;va_start(a,flags);bits=va_arg(a,int);va_end(a);}
    return opening_at(directory,path,flags,bits);
}
ssize_t write(int fd,const void *buf,size_t n) {
    init(); if(!tracked(fd)) return real_write(fd,buf,n);
    unsigned call=writes++;
    if(mode("write") || (mode("partial") && call>0)) {
        note("write-injected",fd,n,-1,EIO);errno=EIO;return -1;
    }
    if(mode("eintr") && call==0) {
        note("write-injected",fd,n,-1,EINTR);errno=EINTR;return -1;
    }
    size_t count=((mode("short") || mode("partial")) && call==0 && n>3)?3:n;
    ssize_t result=real_write(fd,buf,count);int error=result<0?errno:0;
    note("write-real",fd,n,result,error);return result;
}
int fsync(int fd) {
    init();if(!tracked(fd)) return real_sync(fd);
    if(mode("sync")) {note("fsync-injected",fd,0,-1,EIO);errno=EIO;return -1;}
    int result=real_sync(fd),error=result<0?errno:0;note("fsync-real",fd,0,result,error);return result;
}
int flock(int fd,int operation) {
    init();if(!tracked(fd)) return real_lock(fd,operation);
    if(mode("lock") && operation==LOCK_EX && locks++==0) {
        note("flock-injected",fd,operation,-1,EIO);errno=EIO;return -1;
    }
    int result=real_lock(fd,operation),error=result<0?errno:0;note("flock-real",fd,operation,result,error);return result;
}
int close(int fd) {
    init();if(!tracked(fd)) return real_close(fd);
    targets[fd]=0;int result=real_close(fd),error=result<0?errno:0;
    note("close-real",fd,0,result,error);
    if(mode("close") && closes++==0) {note("close-injected",fd,0,-1,EIO);errno=EIO;return -1;}
    return result;
}
int rename(const char *source,const char *destination) {
    init();if(!target(source)) return real_rename(source,destination);
    if(mode("rename")) {note("rename-injected",-1,0,-1,EIO);errno=EIO;return -1;}
    int result=real_rename(source,destination),error=result<0?errno:0;
    note("rename-real",-1,0,result,error);return result;
}
