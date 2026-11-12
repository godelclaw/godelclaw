/* Bounded Unix byte I/O for the native diagnostic script. No agent policy. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

static void fail(const char *what){perror(what);exit(2);}
static int connection(const char *path,int type){
    struct sockaddr_un address={.sun_family=AF_UNIX};
    if(strlen(path)>=sizeof(address.sun_path)){errno=ENAMETOOLONG;fail("socket path");}
    memcpy(address.sun_path,path,strlen(path)+1);
    int fd=socket(AF_UNIX,type,0);if(fd<0)fail("socket");
    struct timeval wait={.tv_sec=5};
    if(setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&wait,sizeof(wait)) ||
       setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&wait,sizeof(wait)))fail("timeout");
    if(connect(fd,(struct sockaddr *)&address,sizeof(address)))fail("connect");
    return fd;
}
static int digit(char c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;}
int main(int argc,char **argv){
    if(argc!=5){fprintf(stderr,"native-probe-io packet|line SOCKET PAYLOAD CAPACITY\n");return 2;}
    signal(SIGPIPE,SIG_IGN);char *end=NULL;unsigned long capacity=strtoul(argv[4],&end,10);
    if(!end||*end||!capacity||capacity>1048576){errno=EINVAL;fail("capacity");}
    unsigned char *bytes=malloc(capacity);if(!bytes)fail("allocate");
    if(!strcmp(argv[1],"packet")){
        size_t hex=strlen(argv[3]);if(hex%2||hex>8192){errno=EINVAL;fail("hex packet");}
        unsigned char request[4096];
        for(size_t i=0;i<hex/2;i++){int a=digit(argv[3][2*i]),b=digit(argv[3][2*i+1]);
            if(a<0||b<0){errno=EINVAL;fail("hex digit");}request[i]=(unsigned char)(a*16+b);}
        int fd=connection(argv[2],SOCK_SEQPACKET);
        if(send(fd,request,hex/2,MSG_NOSIGNAL)!=(ssize_t)(hex/2))fail("send packet");
        struct iovec vector={.iov_base=bytes,.iov_len=capacity};
        struct msghdr message={.msg_iov=&vector,.msg_iovlen=1};
        ssize_t count=recvmsg(fd,&message,MSG_TRUNC);if(count<0)fail("receive packet");close(fd);
        printf("{\"packet_bytes\":%zd,\"prefix_hex\":\"",count);
        size_t have=(size_t)count<capacity?(size_t)count:capacity;
        for(size_t i=0;i<have;i++)printf("%02x",bytes[i]);
        puts("\"}");
    }else if(!strcmp(argv[1],"line")){
        size_t length=strlen(argv[3]);if(length>capacity){errno=EMSGSIZE;fail("outgoing frame");}
        int fd=connection(argv[2],SOCK_STREAM);const char *input=argv[3];
        while(length){ssize_t sent=send(fd,input,length,MSG_NOSIGNAL);if(sent<0){if(errno==EINTR)continue;fail("send");}
            if(!sent){errno=EIO;fail("send");}input+=sent;length-=(size_t)sent;}
        size_t have=0;int complete=0;
        while(have<capacity){ssize_t n=recv(fd,bytes+have,capacity-have,0);if(n<0){if(errno==EINTR)continue;fail("receive");}if(!n)break;
            unsigned char *newline=memchr(bytes+have,'\n',(size_t)n);have+=(size_t)n;
            if(newline){have=(size_t)(newline-bytes)+1;complete=1;break;}}
        close(fd);if(!complete){errno=EMSGSIZE;fail("incomplete or oversized frame");}
        if(fwrite(bytes,1,have,stdout)!=have)fail("write response");
    }else{errno=EINVAL;fail("mode");}
    free(bytes);return 0;
}
