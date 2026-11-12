/* Loopback-only HTTP fixture. CeTTa owns orchestration and assertions. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static void fail(const char *what) { perror(what); exit(2); }
static FILE *file(const char *root, const char *name, const char *mode) {
    char path[4096];
    if (snprintf(path,sizeof(path),"%s/%s",root,name)>=(int)sizeof(path)) { errno=ENAMETOOLONG;fail("path"); }
    FILE *out=fopen(path,mode); if(!out)fail(path); return out;
}
static void write_number(const char *root,const char *name,unsigned long value) {
    FILE *out=file(root,name,"w");fprintf(out,"%lu\n",value);if(fclose(out))fail("close");
}
static char *read_file(const char *root,const char *name,size_t *length) {
    FILE *in=file(root,name,"rb");if(fseek(in,0,SEEK_END))fail("seek");long n=ftell(in);
    if(n<0 || n>16777216){errno=EFBIG;fail("fixture size");}
    rewind(in);
    char *data=malloc((size_t)n+1);if(!data)fail("malloc");
    if(fread(data,1,(size_t)n,in)!=(size_t)n)fail("read");
    fclose(in);data[n]=0;*length=(size_t)n;return data;
}
static int send_all(int fd,const char *data,size_t length) {
    while(length){ssize_t n=send(fd,data,length,MSG_NOSIGNAL);if(n<0){if(errno==EINTR)continue;return 0;}if(n==0)return 0;data+=n;length-=(size_t)n;}return 1;
}
static int measure(int argc,char **argv) {
    if(argc<5 || strcmp(argv[3],"--")){fprintf(stderr,"--measure REPORT -- PROGRAM ARGS...\n");return 2;}
    struct timespec start,end;
    if(clock_gettime(CLOCK_MONOTONIC,&start))fail("clock");
    pid_t child=fork();if(child<0)fail("fork");
    if(child==0){execvp(argv[4],argv+4);perror("exec");_exit(127);}
    int status;struct rusage usage;
    while(wait4(child,&status,0,&usage)<0){if(errno!=EINTR)fail("wait4");}
    if(clock_gettime(CLOCK_MONOTONIC,&end))fail("clock");
    long long elapsed=(long long)(end.tv_sec-start.tv_sec)*1000000000LL+end.tv_nsec-start.tv_nsec;
    int code=WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);
    FILE *report=fopen(argv[2],"w");if(!report)fail("measurement report");
    fprintf(report,"{\"elapsed_ns\":%lld,\"peak_rss_kib\":%ld,\"exit\":%d}\n",elapsed,usage.ru_maxrss,code);
    if(fclose(report))fail("measurement close");
    return code;
}
int main(int argc,char **argv) {
    if(argc>1 && !strcmp(argv[1],"--measure"))return measure(argc,argv);
    if(argc>1 && !strcmp(argv[1],"--user")) {
        if(argc>=3 && !strcmp(argv[2],"is-active")){puts("active");return 0;}
        return 1;
    }
    if(argc!=2){fprintf(stderr,"native-http-peer PRIVATE_DIRECTORY\n");return 2;}
    signal(SIGPIPE,SIG_IGN);const char *root=argv[1];
    int server=socket(AF_INET,SOCK_STREAM,0);if(server<0)fail("socket");
    struct sockaddr_in address={.sin_family=AF_INET,.sin_addr.s_addr=htonl(INADDR_LOOPBACK),.sin_port=0};
    if(bind(server,(struct sockaddr *)&address,sizeof(address))||listen(server,16))fail("listen");
    socklen_t width=sizeof(address);if(getsockname(server,(struct sockaddr *)&address,&width))fail("getsockname");
    write_number(root,"count",0);write_number(root,"port",ntohs(address.sin_port));
    for(unsigned long count=1;;count++) {
        int client=accept(server,NULL,NULL);if(client<0){if(errno==EINTR)continue;fail("accept");}
        struct timeval timeout={.tv_sec=10};setsockopt(client,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        size_t used=0,capacity=16777216u+65536u;char *request=malloc(capacity+1);if(!request)fail("malloc");
        char *boundary=NULL;
        while(!boundary && used<65536u){ssize_t n=recv(client,request+used,65536u-used,0);if(n<=0)break;used+=(size_t)n;request[used]=0;boundary=strstr(request,"\r\n\r\n");}
        if(!boundary){free(request);close(client);count--;continue;}
        size_t header=(size_t)(boundary-request)+4u,length=0;
        for(char *row=request;row<boundary;){char *end=strstr(row,"\r\n");if(!end)break;
            if(!strncasecmp(row,"Content-Length:",15))length=(size_t)strtoull(row+15,NULL,10);
            row=end+2;}
        if(length>16777216u){free(request);close(client);count--;continue;}
        while(used<header+length){ssize_t n=recv(client,request+used,header+length-used,0);if(n<=0)break;used+=(size_t)n;}
        if(used!=header+length){free(request);close(client);count--;continue;}
        char name[64];snprintf(name,sizeof(name),"request-%lu.headers",count);FILE *out=file(root,name,"wb");fwrite(request,1,header,out);fclose(out);
        snprintf(name,sizeof(name),"request-%lu.body",count);out=file(root,name,"wb");fwrite(request+header,1,length,out);fclose(out);free(request);write_number(root,"count",count);
        size_t n=0;char *setting=read_file(root,"delay-ms",&n);long delay=strtol(setting,NULL,10);free(setting);
        if(delay>0){struct timespec pause={.tv_sec=delay/1000,.tv_nsec=(delay%1000)*1000000L};while(nanosleep(&pause,&pause)&&errno==EINTR){}}
        setting=read_file(root,"status",&n);int status=(int)strtol(setting,NULL,10);free(setting);
        if(status==0){close(client);continue;}
        /* A scripted reply for this request number takes precedence. */
        snprintf(name,sizeof(name),"response-%lu.body",count);
        char scripted[4096];snprintf(scripted,sizeof(scripted),"%s/%s",root,name);
        char *body=read_file(root,access(scripted,R_OK)==0?name:"response.body",&n);char response[256];int head=snprintf(response,sizeof(response),"HTTP/1.1 %d Fixture\r\nContent-Length: %zu\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n",status,n);
        if(head>0 && send_all(client,response,(size_t)head))send_all(client,body,n);
        free(body);close(client);
    }
}
